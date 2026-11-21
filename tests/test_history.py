"""查询历史功能验证（零 Key / 零网络 / 临时库）。

覆盖：
  - 建表幂等、落库与按会话隔离
  - 列表按时间倒序、单条回放、删除
  - 404（记录不存在 / 不属于当前会话）
  - 跨会话越权访问被拒

运行：
    python tests/test_history.py
    pytest tests/test_history.py -v
"""
import os
import sys
import tempfile

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backend"))

os.environ["AGENT_MODE"] = "mock"
_TMP = tempfile.mkdtemp(prefix="travel_hist_test_")
os.environ["TRIP_HISTORY_DB"] = os.path.join(_TMP, "test_history.db")

from fastapi.testclient import TestClient

from app import app
import history

client = TestClient(app)

SID = "session-alice"


def _plan(dest="北京", sid=SID):
    return client.post("/api/trip/plan", json={
        "destination": dest, "days": 2, "session_id": sid,
    })


def test_db_setup():
    assert os.path.exists(os.environ["TRIP_HISTORY_DB"])
    history.init_db()
    history.init_db()  # 幂等
    print("TEST_1_OK 建表幂等")


def test_save_and_isolation():
    history.save({"id": "r1", "session_id": SID, "destination": "北京", "days": 3,
                  "preferences": ["历史", "美食"], "ok": True, "created_at": "2026-01-01T00:00:00+00:00"})
    history.save({"id": "r2", "session_id": "session-bob", "destination": "上海", "days": 2,
                  "ok": True, "created_at": "2026-01-01T00:00:00+00:00"})
    assert len(history.list_by_session(SID)) == 1
    assert history.list_by_session(SID)[0]["destination"] == "北京"
    assert history.list_by_session("session-bob")[0]["destination"] == "上海"
    print("TEST_2_OK 落库与会话隔离")


def test_list_order():
    history.save({"id": "old", "session_id": "session-order", "destination": "A", "days": 1,
                  "ok": True, "created_at": "2026-01-01T00:00:00+00:00"})
    history.save({"id": "new", "session_id": "session-order", "destination": "B", "days": 1,
                  "ok": True, "created_at": "2026-06-01T00:00:00+00:00"})
    items = history.list_by_session("session-order")
    assert [x["id"] for x in items] == ["new", "old"]
    print("TEST_3_OK 按时间倒序")


def test_replay_and_deny():
    item = history.get_one("r1", SID)
    assert item is not None and item["destination"] == "北京"
    assert history.get_one("r1", "session-bob") is None  # 越权访问被拒
    print("TEST_4_OK 回放与越权拦截")


def test_delete():
    assert history.delete_one("r2", "session-bob") is True
    assert history.delete_one("r2", "session-bob") is False  # 第二次删不到
    print("TEST_5_OK 删除幂等")


def test_api_end_to_end():
    """走完整 HTTP 链路：规划自动落库 -> 列表 -> 回放 -> 删除。"""
    r = _plan(dest="杭州")
    assert r.status_code == 200, r.text
    items = client.get("/api/history", params={"session_id": SID}).json()["items"]
    assert items and items[0]["destination"] == "杭州"
    assert items[0]["ok"] is True and items[0]["n_attractions"] > 0
    rid = items[0]["id"]

    one = client.get(f"/api/history/{rid}", params={"session_id": SID})
    assert one.status_code == 200
    assert one.json()["plan_json"]  # 有完整行程可回放

    # 跨会话访问 -> 404
    deny = client.get(f"/api/history/{rid}", params={"session_id": "session-bob"})
    assert deny.status_code == 404
    assert deny.json()["error"]["code"] == "NOT_FOUND"

    dele = client.delete(f"/api/history/{rid}", params={"session_id": SID})
    assert dele.status_code == 200
    assert client.get(f"/api/history/{rid}", params={"session_id": SID}).status_code == 404
    print("TEST_6_OK 端到端 HTTP 链路")


def test_validate_missing_session():
    assert client.get("/api/history").status_code == 422
    print("TEST_7_OK 缺 session_id 返回 422")


if __name__ == "__main__":
    test_db_setup()
    test_save_and_isolation()
    test_list_order()
    test_replay_and_deny()
    test_delete()
    test_api_end_to_end()
    test_validate_missing_session()
    print("ALL_HISTORY_TESTS_OK")


def teardown_module():
    """pytest 会话结束清理临时库；Windows 下若句柄未释放则忽略。"""
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
