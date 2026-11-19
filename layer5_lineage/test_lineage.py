"""Run:  .venv/bin/python -m layer5_lineage.test_lineage"""
import json
import os
import tempfile
import time

from .registry import LineageRegistry, ref


def _reg():
    r = LineageRegistry()
    r.add_borrower("B1", name="Ramesh Kumar")
    r.add_borrower("B2", name="ramesh  kumar")               # same name, different case/spaces -> collision
    r.add_conversation("B1", "c1")
    r.register("B1", ref("log", "L1"), conversation_id="c1", token="<PHONE_1>")
    r.register("B1", ref("chunk", "V1"), parent=ref("log", "L1"), relation="chunked_from", conversation_id="c1")
    r.register("B1", ref("emb", "V1"), parent=ref("chunk", "V1"), relation="embedding_of")
    r.register("B1", ref("eval", "E1"), parent=ref("log", "L1"), relation="eval_item_from")
    r.register("B1", ref("vault", "<PHONE_1>@c1"), parent=ref("log", "L1"), relation="vault_entry_for")
    return r


def test_known_for_walks_the_whole_tree():
    k = _reg().known_for("B1")
    assert k["log"] == ["L1"] and k["chunk"] == ["V1"] and k["emb"] == ["V1"] and k["eval"] == ["E1"]
    assert k["vault"] == ["<PHONE_1>@c1"]


def test_descendants_are_transitive():
    r = _reg()
    assert ref("emb", "V1") in r.descendants(ref("log", "L1"))


def test_inverted_indexes():
    r = _reg()
    assert r.refs_for_token("<PHONE_1>") == ["log:L1"] and "log:L1" in r.refs_for_conversation("c1")
    assert r.borrower_of("c1") == "B1"


def test_name_collision_detected_by_keyed_hash_without_raw_names():
    r = _reg()
    assert r.borrowers_sharing_name("B1") == ["B2"]
    assert "ramesh" not in str(r.name_hash).lower()


def test_save_load_and_forget():
    r = _reg()
    p = os.path.join(tempfile.mkdtemp(), "reg.json")
    r.save(p)
    r2 = LineageRegistry.load(p)
    assert r2.known_for("B1") == r.known_for("B1")
    r2.forget_borrower("B1")
    assert r2.known_for("B1")["log"] == [] and r2.borrower_of("c1") is None


# ---------------- optimisation pass ----------------
def _big_registry(n_other=20000):
    r = _reg()
    for i in range(n_other):                                   # many OTHER borrowers' refs: known_for must not scan them
        r.register(f"X{i % 500}", ref("log", f"X{i}"), conversation_id=f"cx{i % 500}", token="<PHONE_9>")
    return r


def test_known_for_uses_per_borrower_map_not_a_scan():
    r = _big_registry()
    assert r.borrower_refs["B1"] == {"log:L1", "chunk:V1", "emb:V1", "eval:E1", "vault:<PHONE_1>@c1"}
    t = time.perf_counter()
    for _ in range(200):
        k = r.known_for("B1")
    per_ms = (time.perf_counter() - t) / 200 * 1000
    print(f"   known_for with 20k unrelated refs: {per_ms:.4f} ms")
    assert k["log"] == ["L1"] and per_ms < 1.0
    assert r.known_for("nobody") == {k_: [] for k_ in ("log", "chunk", "emb", "eval", "vault")}


def _all_ids(r):
    """Every string the registry still holds anywhere (keys and values of every index)."""
    out = set(r.owner) | set(r.ref_metadata) | set(r.ref_tokens) | set(r.ref_conv) | set(r.parents) | set(r.children)
    for idx in (r.children, r.parents, r.token_index, r.conv_index, r.ref_tokens, r.ref_conv):
        for v in idx.values():
            out |= set(v)
    for p, c in r.relation:
        out |= {p, c}
    for s in r.borrower_refs.values():
        out |= s
    return out


def test_forget_borrower_leaves_no_ghost_ids_anywhere():
    r = _reg()
    r.register("B2", ref("log", "L2"), conversation_id="c2", token="<PHONE_1>", purpose="service")     # other borrower, same token
    r.add_conversation("B2", "c2")
    r.forget_borrower("B1")
    ghosts = {x for x in _all_ids(r) if x in {"log:L1", "chunk:V1", "emb:V1", "eval:E1", "vault:<PHONE_1>@c1", "c1"}}
    assert not ghosts, ghosts
    assert "B1" not in r.borrower_refs and "B1" not in r.conversations and "B1" not in str(r.name_index)
    assert not any(p in ("log:L1", "chunk:V1") or c in ("emb:V1", "eval:E1") for p, c in r.relation)
    assert r.refs_for_token("<PHONE_1>") == ["log:L2"]                  # the OTHER borrower's entry survives
    assert r.known_for("B2")["log"] == ["L2"] and r.borrowers_sharing_name("B2") == []


def test_forget_removes_unowned_linked_descendants_but_keeps_other_borrowers_refs():
    r = LineageRegistry(b"k")
    r.register("A", "log:1")
    r.link("log:1", "chunk:orphan")                                      # linked but never registered by anyone
    r.register("B", "eval:9", parent="log:1")                            # another borrower's item hanging below A's log
    r.forget_borrower("A")
    assert "chunk:orphan" not in _all_ids(r) and "log:1" not in _all_ids(r)
    assert r.owner == {"eval:9": "B"} and r.parents.get("eval:9", set()) == set()


def test_save_is_atomic_a_crash_keeps_the_previous_file():
    r = _reg()
    path = os.path.join(tempfile.mkdtemp(), "reg.json")
    r.save(path)
    before = open(path, encoding="utf-8").read()
    r.register("B1", ref("log", "L2"))
    real = os.replace
    try:
        def boom(a, b): raise OSError("crash before rename")
        os.replace = boom
        try:
            r.save(path)
            raise AssertionError("expected the simulated crash")
        except OSError:
            pass
    finally:
        os.replace = real
    assert open(path, encoding="utf-8").read() == before                 # old file untouched
    assert LineageRegistry.load(path).known_for("B1")["log"] == ["L1"]   # and still loads
    r.save(path)                                                          # a leftover .tmp does not block the next save
    assert LineageRegistry.load(path).known_for("B1")["log"] == ["L1", "L2"]


def test_register_write_extracts_tokens_and_keeps_purpose_and_consent():
    r = LineageRegistry(b"k")
    x = r.register_write("B1", "logs", "L7", masked_text="call me on <PHONE_1> about <PERSON_2>, ref 12", conversation_id="c1",
                         purpose="service", consent_id="C1")
    assert x == "log:L7" and r.refs_for_token("<PHONE_1>") == ["log:L7"] and r.refs_for_token("<PERSON_2>") == ["log:L7"]
    assert r.ref_metadata["log:L7"] == {"purpose": "service", "consent_id": "C1"}
    assert "about" not in json.dumps(r.ref_tokens.get("log:L7") and sorted(r.ref_tokens["log:L7"]))   # tokens only, no text
    assert r.register_write("B1", "vector_store", "V1", parent=x, purpose="service") == "chunk:V1"
    try:
        r.register_write("B1", "mystery", "Z"); raise AssertionError("unknown store must raise")
    except ValueError:
        pass
    r.register("B1", "log:L7", token="<PAN_1>")                          # re-register without purpose keeps the earlier tag
    assert r.ref_metadata["log:L7"]["purpose"] == "service" and r.refs_for_token("<PAN_1>") == ["log:L7"]


def test_purpose_scoped_known_for_over_includes_untagged():
    r = LineageRegistry(b"k")
    r.register("B", "log:a", purpose="service")
    r.register("B", "log:b", purpose="collections")
    r.register("B", "log:c")                                              # no purpose recorded
    assert r.known_for("B", purpose="service")["log"] == ["a", "c"]
    assert r.known_for("B")["log"] == ["a", "b", "c"]


def test_ref_cannot_be_claimed_by_two_borrowers():
    r = LineageRegistry(b"k")
    r.register("A", "log:1")
    try:
        r.register("B", "log:1"); raise AssertionError("must raise")
    except ValueError as e:
        assert "ref_owned_by_another_borrower" in str(e)
    assert r.owner["log:1"] == "A" and "log:1" not in r.borrower_refs.get("B", set())


def test_hmac_key_from_env_explicit_wins_and_wrong_key_load_is_refused():
    old = os.environ.get("LINEAGE_HMAC_KEY")
    try:
        os.environ["LINEAGE_HMAC_KEY"] = "env-key-1"
        a = LineageRegistry()
        assert a.key == b"env-key-1" and not a.using_dev_key
        assert LineageRegistry(b"explicit").key == b"explicit"
        a.add_borrower("B", name="Asha Rao")
        path = os.path.join(tempfile.mkdtemp(), "k.json")
        a.save(path)
        assert LineageRegistry.load(path).name_hash == a.name_hash       # same env key -> loads
        os.environ["LINEAGE_HMAC_KEY"] = "env-key-2"
        try:
            LineageRegistry.load(path); raise AssertionError("must refuse a different key")
        except ValueError as e:
            assert "lineage_key_mismatch" in str(e)
        del os.environ["LINEAGE_HMAC_KEY"]
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assert LineageRegistry().using_dev_key and any("DEV HMAC key" in str(x.message) for x in w)
    finally:
        if old is None:
            os.environ.pop("LINEAGE_HMAC_KEY", None)
        else:
            os.environ["LINEAGE_HMAC_KEY"] = old


def test_save_load_round_trip_keeps_metadata_and_rebuilds_reverse_maps():
    r = _reg()
    r.register_write("B1", "logs", "L9", masked_text="<PHONE_1>", conversation_id="c1", purpose="service", consent_id="C1")
    path = os.path.join(tempfile.mkdtemp(), "reg.json")
    r.save(path)
    r2 = LineageRegistry.load(path, key=r.key)
    assert r2.ref_metadata == r.ref_metadata and r2.borrower_refs == r.borrower_refs
    r2.forget_borrower("B1")
    assert not ({"log:L1", "log:L9", "c1"} & _all_ids(r2))               # reverse maps were rebuilt, so forget is clean after load


def test_old_format_file_without_new_fields_still_loads():
    path = os.path.join(tempfile.mkdtemp(), "old.json")
    d = {"conversations": {"B1": ["c1"]}, "owner": {"log:L1": "B1"}, "children": {"log:L1": ["chunk:V1"]},
         "relation": [["log:L1", "chunk:V1", "chunked_from"]], "token_index": {"<PHONE_1>": ["log:L1"]},
         "conv_index": {"c1": ["log:L1"]}, "name_hash": {}}
    json.dump(d, open(path, "w"))
    r = LineageRegistry.load(path)
    assert r.known_for("B1")["chunk"] == ["V1"] and r.ref_metadata == {}
    r.forget_borrower("B1")
    assert not _all_ids(r)


def test_concurrent_registration_is_safe():
    import threading
    r = LineageRegistry(b"k")
    def work(b):
        for i in range(500):
            r.register(b, f"log:{b}-{i}", conversation_id=f"c{b}", token="<PHONE_1>")
    ts = [threading.Thread(target=work, args=(f"T{i}",)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(len(v) for v in r.borrower_refs.values()) == 4000 and len(r.refs_for_token("<PHONE_1>")) == 4000


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
