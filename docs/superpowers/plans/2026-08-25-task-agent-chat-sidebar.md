# Task Agent Chat Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变可信卫士任务执行和右侧监控的前提下，实现可收起的会话栏、标题搜索弹窗、图标化会话菜单和服务端持久化置顶。

**Architecture:** 保持现有三栏页面与会话接口，在 Supervisor 会话摘要和更新请求上增加置顶字段，并让各存储实现统一排序和更新语义。前端继续由 `TaskAgentPage` 管理会话状态，搜索复用已加载的最近 50 条摘要，避免新接口和通用聊天抽象。

**Tech Stack:** React 19、TypeScript、Vite、Playwright、FastAPI、Pydantic、Python、Redis、MySQL、pytest

**Spec:** `docs/superpowers/specs/2026-08-25-task-agent-chat-sidebar-design.md`

## Global Constraints

- 右侧任务状态与执行轨迹在桌面布局中继续常驻。
- 搜索仅匹配前端已加载的最近 50 条会话标题，不增加全文搜索接口。
- 菜单仅包含重命名、置顶或取消置顶、删除。
- 不增加语音、联网搜索、插件、分享、归档或快捷指令。
- 沿用现有主题、盾牌标识和状态色，不复制 ChatGPT 品牌表达。
- 当前工作区已有未提交且与目标文件重叠的改动；每一步都必须先检查差异，不得通过整文件回退或整文件暂存覆盖用户改动。

---

## File Structure

- Modify: `supervisor/app/domain/models.py` — 会话摘要与更新请求契约。
- Modify: `supervisor/app/stores/conversation_store.py` — 内存、Redis、MySQL、组合存储的置顶元数据、排序和更新。
- Modify: `supervisor/app/main.py` — 返回被更新的目标会话摘要。
- Modify: `tests/unit/supervisor/test_conversation.py` — 存储排序、更新、删除和用户隔离回归测试。
- Modify: `frontend/src/shared/lib/api.ts` — 前端摘要类型与通用更新请求。
- Modify: `frontend/src/features/agent/TaskAgentPage.tsx` — 收展状态、搜索弹窗、置顶菜单和失败反馈。
- Modify: `frontend/src/index.css` — 260px/56px 两态布局、弹窗与图标菜单样式。
- Create: `frontend/tests/task-agent-chat-sidebar.spec.ts` — 浏览器层会话栏交互测试。

## Task 1: Define and Verify the Conversation Pin Contract

**Files:**
- Modify: `tests/unit/supervisor/test_conversation.py`
- Modify: `supervisor/app/domain/models.py`
- Modify: `supervisor/app/stores/conversation_store.py`
- Modify: `supervisor/app/main.py`

**Interfaces:**
- Produces: `ConversationSummary.pinned: bool` and `ConversationSummary.pinned_at: str | None`.
- Produces: `ConversationUpdateRequest(title: str | None, pinned: bool | None)` with at least one field required.
- Produces: `ConversationStore.update_conversation(conversation_id, actor_id, title=None, pinned=None) -> ConversationSummary | None`.
- Produces: `ConversationStore.delete_conversation(conversation_id, actor_id) -> bool` for every backend.

- [ ] **Step 1: Add failing in-memory store tests**

Append tests that create `conv-old` and `conv-new`, pin `conv-old`, and assert it sorts first without changing the other actor:

```python
def test_memory_conversation_store_pins_updates_and_deletes_per_actor():
    store = InMemoryConversationStore()
    for conversation_id, actor_id in [("conv-old", "actor-1"), ("conv-new", "actor-1"), ("conv-other", "actor-2")]:
        store.append_message(
            conversation_id,
            actor_id,
            ConversationMessage(id=f"user-{conversation_id}", role="user", text=conversation_id),
        )

    pinned = store.update_conversation("conv-old", "actor-1", title="重要扫描", pinned=True)

    assert pinned is not None
    assert pinned.title == "重要扫描"
    assert pinned.pinned is True
    assert pinned.pinned_at is not None
    assert [item.conversation_id for item in store.list("actor-1")] == ["conv-old", "conv-new"]
    assert store.update_conversation("conv-old", "actor-2", pinned=False) is None
    assert store.delete_conversation("conv-old", "actor-2") is False
    assert store.delete_conversation("conv-old", "actor-1") is True
    assert store.get("conv-old", "actor-1") is None
```

Add an API test that pins a non-leading conversation and verifies that the returned summary belongs to the requested ID, then unpins it and rejects an empty patch:

```python
def test_conversation_patch_returns_target_summary_and_validates_body(monkeypatch):
    store = InMemoryConversationStore()
    monkeypatch.setattr(main, "_conversations", store)
    for conversation_id in ["conv-old", "conv-new"]:
        store.append_message(
            conversation_id,
            "actor-1",
            ConversationMessage(id=f"user-{conversation_id}", role="user", text=conversation_id),
        )
    client = TestClient(main.app)
    headers = {"X-Actor-Id": "actor-1"}

    pinned = client.patch("/v1/conversations/conv-old", headers=headers, json={"pinned": True})
    unpinned = client.patch("/v1/conversations/conv-old", headers=headers, json={"pinned": False})
    empty = client.patch("/v1/conversations/conv-old", headers=headers, json={})
    missing = client.patch("/v1/conversations/missing", headers=headers, json={"pinned": True})

    assert pinned.status_code == 200
    assert pinned.json()["conversationId"] == "conv-old"
    assert pinned.json()["pinned"] is True
    assert pinned.json()["pinnedAt"] is not None
    assert unpinned.json()["pinned"] is False
    assert unpinned.json()["pinnedAt"] is None
    assert empty.status_code == 422
    assert missing.status_code == 404
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
pytest -c tests/pytest.ini tests/unit/supervisor/test_conversation.py -q
```

Expected: the new tests fail because `pinned`, optional update fields, and in-memory update/delete are not implemented.

- [ ] **Step 3: Implement the minimal domain and in-memory behavior**

Use these model shapes:

```python
class ConversationSummary(ApiModel):
    # existing fields remain
    pinned: bool = False
    pinned_at: str | None = None


class ConversationUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None

    @model_validator(mode="after")
    def require_update(self):
        if self.title is None and self.pinned is None:
            raise ValueError("title or pinned is required")
        return self
```

Extend `Conversation` with `title`, `pinned`, and `pinned_at`; make `_conversation_summary` prefer the stored title. Sort `InMemoryConversationStore.list` with a descending key equivalent to `(item.pinned, item.pinned_at or epoch, item.updated_at, item.updated_order)`. Implement actor-scoped update/delete and return the updated summary.

Change the route to use the returned target directly:

```python
updated = _conversations.update_conversation(
    conversation_id,
    actor_id,
    title=req.title,
    pinned=req.pinned,
)
if updated is None:
    raise HTTPException(status_code=404, detail="Conversation not found")
return updated
```

- [ ] **Step 4: Run focused tests and confirm pass**

Run the same pytest command. Expected: all tests in `test_conversation.py` pass.

- [ ] **Step 5: Review the exact diff checkpoint**

Run:

```bash
git diff -- supervisor/app/domain/models.py supervisor/app/stores/conversation_store.py supervisor/app/main.py tests/unit/supervisor/test_conversation.py
```

Confirm no existing message, task, draft, or workflow behavior changed. Because these files already contain user changes, do not stage or commit the whole files unless the resulting staged diff contains only this task's intended hunks.

## Task 2: Persist Pin Metadata in Redis and MySQL

**Files:**
- Modify: `supervisor/app/stores/conversation_store.py`
- Modify: `tests/unit/supervisor/test_conversation.py`

**Interfaces:**
- Consumes: `Conversation` metadata and `update_conversation(...) -> ConversationSummary | None` from Task 1.
- Produces: Redis metadata hash key `supervisor:conversation:{actor_id}:{conversation_id}:meta`.
- Produces: MySQL columns `is_pinned TINYINT(1) NOT NULL DEFAULT 0` and `pinned_at DATETIME(6) NULL`.

- [ ] **Step 1: Add failing Redis metadata tests**

Create this focused fake Redis client inside `test_conversation.py`; seed its message list directly so the test exercises list/update/delete without recreating pipeline behavior:

```python
import fnmatch
import json


class _ConversationRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def lrange(self, key, _start, _end):
        return list(self.lists.get(str(key), []))

    def scan_iter(self, match, count=10):
        del count
        return iter(key for key in self.lists if fnmatch.fnmatch(key, match))

    def hgetall(self, key):
        return dict(self.hashes.get(str(key), {}))

    def hset(self, key, mapping):
        self.hashes.setdefault(str(key), {}).update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping)

    def expire(self, _key, _seconds):
        return True

    def delete(self, *keys):
        removed = 0
        for key in map(str, keys):
            removed += int(self.lists.pop(key, None) is not None)
            removed += int(self.hashes.pop(key, None) is not None)
        return removed
```

Seed and assert with:

```python
client = _ConversationRedis()
message = ConversationMessage(id="user-1", role="user", text="原始标题")
client.lists["supervisor:conversation:actor-1:conv-1:messages"] = [
    json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
]
store = RedisConversationStore(client)
updated = store.update_conversation("conv-1", "actor-1", title="置顶任务", pinned=True)
restored = store.list("actor-1")[0]

assert updated is not None
assert restored.title == "置顶任务"
assert restored.pinned is True
assert restored.pinned_at is not None
assert store.delete_conversation("conv-1", "actor-1") is True
assert client.hgetall("supervisor:conversation:actor-1:conv-1:meta") == {}
```

- [ ] **Step 2: Run the Redis-focused test and confirm failure**

Run:

```bash
pytest -c tests/pytest.ini tests/unit/supervisor/test_conversation.py -q -k redis
```

Expected: failure because Redis currently stores only messages and derives titles from them.

- [ ] **Step 3: Implement Redis metadata and MySQL schema/query updates**

For Redis, read/write the metadata hash alongside the message list, apply the same TTL to both keys, and delete both keys. Parse `pinned` using the exact stored strings `"1"` and `"0"`; serialize timestamps as UTC ISO strings.

For MySQL, add these idempotent migration statements after the existing title migration:

```sql
ALTER TABLE tg_agent_conversation
ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0 AFTER title
```

```sql
ALTER TABLE tg_agent_conversation
ADD COLUMN pinned_at DATETIME(6) NULL AFTER is_pinned
```

Select both fields into `ConversationSummary`, group by them, and use:

```sql
ORDER BY c.is_pinned DESC, c.pinned_at DESC, c.updated_at DESC, c.id DESC
```

When `pinned` is supplied, update `is_pinned` and set `pinned_at = NOW(6)` for true or `NULL` for false. Implement a targeted summary read so the PATCH response never returns another conversation. Make `MirroredConversationStore.update_conversation` delegate to durable storage and mirror metadata best-effort without changing MySQL authority.

- [ ] **Step 4: Run conversation and compilation checks**

Run:

```bash
pytest -c tests/pytest.ini tests/unit/supervisor/test_conversation.py -q
python -m compileall supervisor/app
```

Expected: tests pass and compilation exits zero.

- [ ] **Step 5: Review the storage diff checkpoint**

Run:

```bash
git diff --check
git diff -- supervisor/app/stores/conversation_store.py tests/unit/supervisor/test_conversation.py
```

Confirm standalone memory, Redis, MySQL, and mirrored modes all satisfy the same update/delete interface. Do not stage unrelated pre-existing hunks.

## Task 3: Extend the Frontend API Contract

**Files:**
- Modify: `frontend/src/shared/lib/api.ts`
- Modify: `frontend/src/features/agent/TaskAgentPage.tsx` — only migrate the existing rename call to the object request in this task.

**Interfaces:**
- Consumes: backend camel-case response fields `pinned` and `pinnedAt`.
- Produces: `ApiTaskAgentConversationSummary.pinned: boolean` and `pinnedAt?: string | null`.
- Produces: `updateTaskAgentConversation(conversationId, update: { title?: string; pinned?: boolean })`.

- [ ] **Step 1: Change the call sites to the object request and confirm TypeScript failure**

Plan the final call shapes before changing the API function:

```ts
await updateTaskAgentConversation(conversationId, { title: renameValue.trim() });
await updateTaskAgentConversation(conversationId, { pinned: !conversation.pinned });
```

Run:

```bash
npm --prefix frontend run build
```

Expected: TypeScript rejects the object request while the API helper still expects a string.

- [ ] **Step 2: Implement the minimal type and API update**

Use:

```ts
export interface ApiTaskAgentConversationSummary {
  // existing fields remain
  pinned: boolean;
  pinnedAt?: string | null;
}

export type TaskAgentConversationUpdate = {
  title?: string;
  pinned?: boolean;
};

export async function updateTaskAgentConversation(
  conversationId: string,
  update: TaskAgentConversationUpdate,
): Promise<ApiTaskAgentConversationSummary> {
  return apiFetch<ApiTaskAgentConversationSummary>(
    `/api/v1/task-agent/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'PATCH', body: JSON.stringify(update), headers: { 'Content-Type': 'application/json' } },
  );
}
```

- [ ] **Step 3: Build and review the API diff**

Run:

```bash
npm --prefix frontend run build
git diff -- frontend/src/shared/lib/api.ts
```

Expected: build passes after all call sites use the object request. Verify no unrelated API helper changed.

## Task 4: Implement and Test the Sidebar Interaction

**Files:**
- Create: `frontend/tests/task-agent-chat-sidebar.spec.ts`
- Modify: `frontend/src/features/agent/TaskAgentPage.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `updateTaskAgentConversation(conversationId, update)` from Task 3.
- Produces accessible controls named `搜索会话`, `收起会话栏`, `展开会话栏`, and `管理 <会话标题>`.
- Produces dialog `role="dialog"` with accessible name `搜索会话`.

- [ ] **Step 1: Add the failing Playwright interaction test**

Seed login and mock the list/detail/update endpoints:

```ts
import { expect, test } from '@playwright/test';

const conversations = [
  { conversationId: 'conv-pinned', title: '生产环境告警研判', preview: '完成', messageCount: 2, createdAt: '2026-08-24T10:00:00Z', updatedAt: '2026-08-24T11:00:00Z', pinned: true, pinnedAt: '2026-08-24T12:00:00Z' },
  { conversationId: 'conv-scan', title: 'S2-045 漏洞验证', preview: '等待确认', messageCount: 2, createdAt: '2026-08-24T09:00:00Z', updatedAt: '2026-08-24T10:00:00Z', pinned: false, pinnedAt: null },
];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sentinel_logged_in_v1', '1'));
  await page.route('**/api/v1/task-agent/conversations?limit=50', route => route.fulfill({ json: { code: 0, message: 'ok', data: conversations } }));
  await page.route(/\/api\/v1\/task-agent\/conversations\/conv-(pinned|scan)$/, async route => {
    if (route.request().method() === 'PATCH') return route.fulfill({ json: { code: 0, message: 'ok', data: conversations[1] } });
    return route.fulfill({ json: { code: 0, message: 'ok', data: { conversationId: route.request().url().endsWith('conv-scan') ? 'conv-scan' : 'conv-pinned', messages: [], taskId: null } } });
  });
});
```

Add assertions that:

```ts
await page.goto('/agent');
await expect(page.getByText('置顶', { exact: true })).toBeVisible();
await page.getByRole('button', { name: '收起会话栏' }).click();
await expect(page.locator('.task-agent-layout')).toHaveClass(/sidebar-collapsed/);
await expect(page.getByRole('button', { name: '展开会话栏' })).toBeVisible();
await page.getByRole('button', { name: '搜索会话' }).click();
const dialog = page.getByRole('dialog', { name: '搜索会话' });
await expect(dialog.getByText('生产环境告警研判')).toBeVisible();
await dialog.getByRole('textbox').fill('S2-045');
await expect(dialog.getByText('S2-045 漏洞验证')).toBeVisible();
await expect(dialog.getByText('生产环境告警研判')).not.toBeVisible();
await page.keyboard.press('Escape');
await page.getByRole('button', { name: '展开会话栏' }).click();
await page.getByRole('button', { name: '管理 S2-045 漏洞验证' }).click();
await expect(page.getByRole('button', { name: '重命名' }).locator('svg')).toHaveCount(1);
await expect(page.getByRole('button', { name: '置顶' }).locator('svg')).toHaveCount(1);
await expect(page.getByRole('button', { name: '删除' }).locator('svg')).toHaveCount(1);
```

- [ ] **Step 2: Run the Playwright test and confirm failure**

Run:

```bash
npm --prefix frontend run test:e2e -- task-agent-chat-sidebar.spec.ts
```

Expected: controls and dialog are absent.

- [ ] **Step 3: Implement the minimal React behavior**

Add only these states:

```ts
const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
const [searchOpen, setSearchOpen] = useState(false);
const [conversationSearch, setConversationSearch] = useState('');
```

Remove the inline sidebar search input. Split `conversations` into `pinnedConversations` and unpinned date groups. Add Lucide `Search`, `PanelLeftClose`, `PanelLeftOpen`, `Pencil`, `Pin`, `PinOff`, and `Trash2` icons using existing `lucide-react`; do not add dependencies.

Use `className={`task-agent-layout${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}`. In collapsed mode render only icon buttons for expand, new conversation, and search. Keep the same `startNewConversation`, `loadConversation`, `refreshConversations`, rename, and delete handlers; add a pin handler that awaits PATCH, closes the menu, refreshes on success, and writes `conversationError` on failure.

Render the search dialog only while open. Give it `role="dialog"`, `aria-modal="true"`, `aria-labelledby="task-agent-search-title"`, an auto-focused title input, recent results from `conversations`, and a document keydown effect that closes on `Escape`. Clicking a result must call `loadConversation`, close the dialog, and clear the query.

- [ ] **Step 4: Implement scoped CSS**

Change the grid through a modifier, not inline widths:

```css
.task-agent-layout.sidebar-collapsed {
  grid-template-columns: 56px minmax(420px, 1fr) 310px;
}

.task-agent-conversations.collapsed {
  padding-inline: 8px;
  align-items: center;
}
```

Add styles for the narrow rail, icon buttons, centered fixed overlay/dialog, search result hover/current states, pinned group label, and icon-text menu rows. Reuse existing CSS variables; do not add new global colors. Preserve current responsive rules and ensure the right monitor column is unchanged at desktop widths.

- [ ] **Step 5: Run the UI test, build, and visual check**

Run:

```bash
npm --prefix frontend run test:e2e -- task-agent-chat-sidebar.spec.ts
npm --prefix frontend run build
```

Expected: the focused browser test and build pass. Capture expanded, collapsed, and search-dialog screenshots at the current desktop viewport and verify no overlap with the right monitor.

- [ ] **Step 6: Review the frontend diff checkpoint**

Run:

```bash
git diff --check
git diff -- frontend/src/features/agent/TaskAgentPage.tsx frontend/src/index.css frontend/src/shared/lib/api.ts frontend/tests/task-agent-chat-sidebar.spec.ts
```

Confirm no task stream, confirmation, Markdown, thinking, tool trace, header, or right-monitor logic changed. Do not stage unrelated pre-existing hunks.

## Task 5: Final Regression Verification

**Files:**
- Verify only; modify a task-owned file only if a failing assertion reveals a defect in this feature.

**Interfaces:**
- Consumes all behavior from Tasks 1–4.
- Produces a verified implementation with no unrelated staged changes.

- [ ] **Step 1: Run backend regression tests**

Run:

```bash
pytest -c tests/pytest.ini tests/unit/supervisor/test_conversation.py tests/unit/gateway/test_task_agent_unit.py -q
python -m compileall supervisor/app gateway/app
```

Expected: all selected tests pass and compilation exits zero.

- [ ] **Step 2: Run frontend regression checks**

Run:

```bash
npm --prefix frontend run build
npm --prefix frontend run test:e2e -- task-agent-chat-sidebar.spec.ts
```

Expected: build and focused Playwright suite pass.

- [ ] **Step 3: Audit scope and working-tree safety**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Confirm the implementation touched only files listed in this plan and that pre-existing user changes remain present. If overlapping dirty files prevent a clean feature-only commit, leave implementation changes unstaged and report that explicitly instead of committing unrelated work.

- [ ] **Step 4: Record completion evidence**

Report the exact commands and pass counts, the screenshots reviewed, and any pre-existing unrelated failures. Do not claim completion if either focused backend tests, the frontend build, or the focused Playwright test fails.
