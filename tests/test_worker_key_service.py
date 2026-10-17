import pytest
from sqlalchemy import text

from app.services import worker_key_service as wks


def test_create_returns_the_raw_key_once_and_stores_only_its_hash(db_session, make_profile):
    uid = make_profile()
    created = wks.create_key(db_session, user_id=uid, name="Asha's MacBook")
    assert created.raw_key.startswith("tqw_") and len(created.raw_key) > 40
    assert created.key.key_hash == wks.hash_key(created.raw_key)
    assert created.key.key_prefix == created.raw_key[:8]
    assert not hasattr(created.key, "raw_key")
    stored = db_session.execute(text("select * from worker_keys")).mappings().one()
    assert created.raw_key not in {str(v) for v in stored.values()}


def test_active_key_matches_only_while_owner_is_approved_and_key_unrevoked(db_session, make_profile):
    uid = make_profile()
    created = wks.create_key(db_session, user_id=uid, name="m")
    assert wks.find_active_key(db_session, created.raw_key).user_id == uid
    db_session.execute(text("update platform.profiles set status='suspended' "
                            "where user_id = cast(:u as uuid)"), {"u": uid})
    db_session.commit()
    assert wks.find_active_key(db_session, created.raw_key) is None
    db_session.execute(text("update platform.profiles set status='approved' "
                            "where user_id = cast(:u as uuid)"), {"u": uid})
    db_session.commit()
    wks.revoke_key(db_session, user_id=uid, key_id=created.key.id)
    assert wks.find_active_key(db_session, created.raw_key) is None


def test_unknown_key_does_not_match(db_session, make_profile):
    make_profile()
    assert wks.find_active_key(db_session, "tqw_not-a-real-key") is None


def test_last_used_is_written_at_most_once_a_minute(db_session, make_profile):
    uid = make_profile()
    created = wks.create_key(db_session, user_id=uid, name="m")
    wks.find_active_key(db_session, created.raw_key)
    first = wks.list_keys(db_session, user_id=uid)[0].last_used_at
    wks.find_active_key(db_session, created.raw_key)
    db_session.expire_all()
    assert wks.list_keys(db_session, user_id=uid)[0].last_used_at == first is not None


def test_at_most_ten_active_keys_and_revoked_ones_do_not_count(db_session, make_profile):
    uid = make_profile()
    keys = [wks.create_key(db_session, user_id=uid, name=f"k{i}") for i in range(10)]
    with pytest.raises(wks.WorkerKeyLimit):
        wks.create_key(db_session, user_id=uid, name="k10")
    wks.revoke_key(db_session, user_id=uid, key_id=keys[0].key.id)
    wks.create_key(db_session, user_id=uid, name="k10")


def test_revoke_is_idempotent(db_session, make_profile):
    uid = make_profile()
    created = wks.create_key(db_session, user_id=uid, name="m")
    first = wks.revoke_key(db_session, user_id=uid, key_id=created.key.id).revoked_at
    assert wks.revoke_key(db_session, user_id=uid, key_id=created.key.id).revoked_at == first


def test_list_is_newest_first_and_only_mine(db_session, make_profile):
    uid, other = make_profile(), make_profile()
    wks.create_key(db_session, user_id=uid, name="a")
    wks.create_key(db_session, user_id=uid, name="b")
    wks.create_key(db_session, user_id=other, name="c")
    assert [k.name for k in wks.list_keys(db_session, user_id=uid)] == ["b", "a"]


def test_cannot_revoke_someone_elses_key(db_session, make_profile):
    owner, other = make_profile(), make_profile()
    created = wks.create_key(db_session, user_id=owner, name="m")
    with pytest.raises(wks.WorkerKeyNotFound):
        wks.revoke_key(db_session, user_id=other, key_id=created.key.id)
