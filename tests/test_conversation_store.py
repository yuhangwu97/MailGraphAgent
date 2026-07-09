import json

from src.storage import conversation_store as cs


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.strings = {}
        self.zsets = {}
        self.lists = {}

    def hset(self, key, field=None, value=None, mapping=None):
        self.hashes.setdefault(key, {})
        if mapping:
            self.hashes[key].update(mapping)
        else:
            self.hashes[key][field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hincrby(self, key, field, amount):
        self.hashes.setdefault(key, {})
        self.hashes[key][field] = str(int(self.hashes[key].get(field, 0)) + amount)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        if end == -1:
            return [k for k, _ in items[start:]]
        return [k for k, _ in items[start:end + 1]]

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def delete(self, key):
        self.hashes.pop(key, None)
        self.strings.pop(key, None)
        self.lists.pop(key, None)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        if start < 0:
            start = max(len(values) + start, 0)
        if end == -1:
            return values[start:]
        return values[start:end + 1]

    def get(self, key):
        return self.strings.get(key)

    def set(self, key, value):
        self.strings[key] = value

    def close(self):
        pass


def make_store(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cs.redis, "Redis", lambda **kwargs: fake)
    store = cs.ConversationStore("acct")
    return store


def test_conversation_sessions_and_messages(monkeypatch):
    store = make_store(monkeypatch)

    session = store.create_session("测试会话")
    store.add_message(session["id"], "user", "最近三天失败邮件")
    store.add_message(session["id"], "assistant", "有 2 封", {"rows": [{"数量": "2"}]})

    sessions = store.list_sessions()
    messages = store.list_messages(session["id"])

    assert sessions[0]["id"] == session["id"]
    assert sessions[0]["message_count"] == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["result"]["rows"][0]["数量"] == "2"


def test_agent_memory_updates_from_turn(monkeypatch):
    store = make_store(monkeypatch)

    store.update_memory_from_turn("以后默认看最近三天", "好的")
    memory = store.get_memory()

    assert memory.preferences
    assert "最近三天" in memory.to_prompt()
    assert json.loads(store.r.get(store._k("memory")))["summary"]
