import pytest

from core.stores import StoreConfigError, load_stores


def write(tmp_path, text):
    p = tmp_path / "stores.toml"
    p.write_text(text)
    return p


def test_repo_stores_file_is_valid():
    stores = load_stores()
    assert [s.id for s in stores][:2] == ["dans", "wheatons"]
    assert {s.tier for s in stores} <= {"cloud", "on-prem"}


def test_cloud_store_defaults_need_only_id_name_tier(tmp_path):
    (s,) = load_stores(write(tmp_path, '[[store]]\nid = "downtown"\nname = "Downtown"\ntier = "cloud"\n'))
    assert s.data == "gs://frame-shop-511616-kpi-warehouse/stores/downtown/warehouse.db"
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


def test_exclude_invoices_for_cloud_stores_reach_the_refresh_matrix(tmp_path, monkeypatch, capsys):
    from core import stores as stores_mod
    p = write(tmp_path, '[[store]]\nid = "a"\nname = "A"\ntier = "cloud"\nexclude_invoices = [1, 3]\n')
    (s,) = load_stores(p)
    assert s.exclude_invoices == (1, 3)
    monkeypatch.setenv("STORES_FILE", str(p))
    stores_mod.main(["--tier", "cloud", "--matrix"])
    assert '"exclude_invoices": "1,3"' in capsys.readouterr().out


@pytest.mark.parametrize("body, msg", [
    ('tier = "on-prem"\ndata = "x.sqlite"\nexclude_invoices = [1]', "for cloud stores"),
    ('tier = "cloud"\nexclude_invoices = ["1"]', "list of invoice numbers"),
])
def test_bad_exclude_invoices_rejected(tmp_path, body, msg):
    with pytest.raises(StoreConfigError, match=msg):
        load_stores(write(tmp_path, f'[[store]]\nid = "a"\nname = "A"\n{body}\n'))


def test_former_ids_are_kept_and_cannot_clash_with_a_current_id(tmp_path):
    (s,) = load_stores(write(tmp_path, '[[store]]\nid = "dans"\nname = "D"\ntier = "cloud"\nformer_ids = ["main"]\n'))
    assert s.former_ids == ("main",)
    with pytest.raises(StoreConfigError, match="duplicate store ids"):
        load_stores(write(tmp_path, '[[store]]\nid = "a"\nname = "A"\ntier = "cloud"\nformer_ids = ["b"]\n'
                                    '[[store]]\nid = "b"\nname = "B"\ntier = "cloud"\n'))
