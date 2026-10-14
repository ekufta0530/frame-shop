import pytest

from core.stores import StoreConfigError, load_stores


def write(tmp_path, text):
    p = tmp_path / "stores.toml"
    p.write_text(text)
    return p


def test_repo_stores_file_is_valid():
    stores = load_stores()
    assert [s.id for s in stores][:2] == ["main", "mason"]
    assert {s.tier for s in stores} <= {"cloud", "on-prem"}


def test_cloud_store_defaults_need_only_id_name_tier(tmp_path):
    (s,) = load_stores(write(tmp_path, '[[store]]\nid = "downtown"\nname = "Downtown"\ntier = "cloud"\n'))
    assert s.data == "gs://lifesaver-kpi-warehouse/stores/downtown/warehouse.db"
    assert s.secret == "lifesaver-store-downtown"
    assert not s.on_prem


def test_on_prem_store_needs_its_data_location(tmp_path):
    with pytest.raises(StoreConfigError, match="on-prem stores need data"):
        load_stores(write(tmp_path, '[[store]]\nid = "x"\nname = "X"\ntier = "on-prem"\n'))


@pytest.mark.parametrize("body, msg", [
    ('id = "all"\nname = "A"\ntier = "cloud"', "reserved"),
    ('id = "Bad Id"\nname = "A"\ntier = "cloud"', "id must be"),
    ('id = "a"\nname = "A"\ntier = "desktop"', "tier must be"),
    ('id = "a"\nname = "A"\ntier = "cloud"\nchart_from = "2024"', "YYYY-MM"),
    ('id = "a"\nname = "A"\ntier = "cloud"\nbaselines = { nope = 1 }', "unknown KPI"),
    ('id = "a"\nname = "A"\ntier = "cloud"\ntypo = 1', "unknown keys"),
])
def test_bad_entries_are_rejected_with_a_reason(tmp_path, body, msg):
    with pytest.raises(StoreConfigError, match=msg):
        load_stores(write(tmp_path, f"[[store]]\n{body}\n"))


def test_duplicate_ids_rejected(tmp_path):
    one = '[[store]]\nid = "a"\nname = "A"\ntier = "cloud"\n'
    with pytest.raises(StoreConfigError, match="duplicate"):
        load_stores(write(tmp_path, one + one))


def test_data_location_can_be_overridden_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_MY_SHOP_DATA", "local.db")
    (s,) = load_stores(write(tmp_path, '[[store]]\nid = "my-shop"\nname = "S"\ntier = "cloud"\n'))
    assert s.data == "local.db"
