import pytest

from stratus.specs.models import ClusterSpec, JobSpec, Resources, ServiceSpec, SpecKind, VolumeSpec
from stratus.specs.store import SpecStore
from stratus.tui.forms import SCHEMAS, FormError, default_form, form_to_spec, spec_to_form


@pytest.fixture
def store(tmp_path):
    return SpecStore(tmp_path / "specs")


def test_save_load_list(store):
    spec = ClusterSpec(name="dev", resources=Resources(cpus="2"))
    path = store.save(spec)
    assert path == store.root / "clusters" / "dev.yaml"
    text = path.read_text()
    assert text.startswith("# Managed by stratus")
    assert "sky launch -c dev" in text
    assert store.get(SpecKind.CLUSTER, "dev") == spec
    assert [s.name for s in store.list()] == ["dev"]
    assert store.names(SpecKind.CLUSTER) == {"dev"}


def test_invalid_file_is_listed_with_error(store):
    path = store.path_for(SpecKind.JOB, "broken")
    path.parent.mkdir(parents=True)
    path.write_text("name: broken\nresources: [1, 2\n")
    (entry,) = store.list(SpecKind.JOB)
    assert entry.spec is None and entry.error
    path.write_text("name: broken\n")  # valid YAML, missing run
    (entry,) = store.list(SpecKind.JOB)
    assert entry.spec is None and "run" in entry.error


def test_save_text_keeps_comments(store):
    text = "# my notes\nname: j\nrun: echo hi  # inline\n"
    spec = store.save_text(SpecKind.JOB, "j", text)
    assert isinstance(spec, JobSpec)
    assert store.path_for(SpecKind.JOB, "j").read_text() == text


def test_save_text_rejects_invalid(store):
    with pytest.raises(Exception):
        store.save_text(SpecKind.JOB, "j", "name: j\n")
    assert not store.exists(SpecKind.JOB, "j")


def test_history(store):
    store.record(SpecKind.CLUSTER, "dev", "launch", "req-1")
    assert (("cluster", "dev") in store.last_used())


@pytest.mark.parametrize("kind", list(SpecKind))
def test_defaults_build_valid_specs(kind):
    values = default_form(kind)
    values["name"] = "demo"
    spec = form_to_spec(kind, values)
    assert spec.name == "demo"
    # form → spec → form → spec is stable
    assert form_to_spec(kind, spec_to_form(spec)) == spec


def test_form_errors_are_keyed_by_field():
    values = default_form(SpecKind.CLUSTER) | {"name": "Bad Name", "cpus": "lots", "envs": "nope", "disk_size": "x"}
    with pytest.raises(FormError) as exc:
        form_to_spec(SpecKind.CLUSTER, values)
    assert {"cpus", "envs", "disk_size"} <= set(exc.value.errors)

    with pytest.raises(FormError) as exc:
        form_to_spec(SpecKind.CLUSTER, default_form(SpecKind.CLUSTER) | {"name": "Bad Name"})
    assert "name" in exc.value.errors


def test_service_model_errors_map_to_fields():
    values = default_form(SpecKind.SERVICE) | {"name": "svc", "port": "http"}
    with pytest.raises(FormError) as exc:
        form_to_spec(SpecKind.SERVICE, values)
    assert "port" in exc.value.errors
    values = default_form(SpecKind.SERVICE) | {"name": "svc", "max_replicas": "3"}
    with pytest.raises(FormError) as exc:
        form_to_spec(SpecKind.SERVICE, values)
    assert "target_qps_per_replica" in exc.value.errors


def test_form_preserves_extra_keys_from_base():
    base = ClusterSpec(name="dev", resources=Resources(cpus="1", extra={"instance_type": "x"}), extra={"file_mounts": {"/a": "b"}})
    values = spec_to_form(base) | {"cpus": "2"}
    spec = form_to_spec(SpecKind.CLUSTER, values, base=base)
    assert spec.resources.cpus == "2"
    assert spec.resources.extra == {"instance_type": "x"}
    assert spec.extra == {"file_mounts": {"/a": "b"}}


def test_every_schema_field_is_handled():
    """Each form field must be read by form_to_spec and written by spec_to_form."""
    for kind, schema in SCHEMAS.items():
        values = default_form(kind) | {"name": "x"}
        spec = form_to_spec(kind, values)
        out = spec_to_form(spec)
        missing = {f.key for f in schema.fields} - set(out)
        assert not missing, (kind, missing)


def test_volume_form():
    spec = form_to_spec(SpecKind.VOLUME, {"name": "v", "type": "k8s-pvc", "infra": "k8s", "size": "5Gi", "labels": "a=b", "access_mode": ""})
    assert isinstance(spec, VolumeSpec) and spec.labels == {"a": "b"} and spec.access_mode is None


def test_service_form_roundtrip():
    spec = ServiceSpec(name="s", run="serve", port=9000, min_replicas=1, max_replicas=4, target_qps_per_replica=3)
    assert form_to_spec(SpecKind.SERVICE, spec_to_form(spec), base=spec) == spec


def test_k8s_autostop_requires_down():
    values = default_form(SpecKind.CLUSTER) | {"name": "c", "infra": "k8s", "autostop_minutes": "30"}
    with pytest.raises(FormError) as exc:
        form_to_spec(SpecKind.CLUSTER, values)
    assert "autostop_down" in exc.value.errors
    spec = form_to_spec(SpecKind.CLUSTER, values | {"autostop_down": True})
    assert spec.autostop_minutes == 30 and spec.autostop_down
    # other clouds can stop
    assert form_to_spec(SpecKind.CLUSTER, values | {"infra": "aws"}).autostop_minutes == 30
