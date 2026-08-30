import { expect, test } from "@playwright/test";

type Conversation = {
  conversationId: string;
  title: string;
  preview: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  pinned: boolean;
  pinnedAt: string | null;
};

const yesterdayUpdatedAt = new Date(Date.now() - 36 * 60 * 60 * 1000).toISOString();

const initialConversations: Conversation[] = [
  {
    conversationId: "conv-pinned",
    title: "生产环境告警研判",
    preview: "完成",
    messageCount: 2,
    createdAt: "2026-08-24T10:00:00Z",
    updatedAt: "2026-08-24T11:00:00Z",
    pinned: true,
    pinnedAt: "2026-08-24T12:00:00Z",
  },
  {
    conversationId: "conv-scan",
    title: "S2-045 漏洞验证",
    preview: "等待确认",
    messageCount: 2,
    createdAt: "2026-08-24T09:00:00Z",
    updatedAt: yesterdayUpdatedAt,
    pinned: false,
    pinnedAt: null,
  },
];

test.beforeEach(async ({ page }) => {
  let conversations = initialConversations.map((conversation) => ({ ...conversation }));

  await page.addInitScript(() => localStorage.setItem("sentinel_logged_in_v1", "1"));
  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/v1/tasks", async (route) => {
    await route.fulfill({ json: { code: 0, message: "ok", data: [] } });
  });
  await page.route("**/api/v1/task-agent/conversations?limit=50", async (route) => {
    await route.fulfill({ json: { code: 0, message: "ok", data: conversations } });
  });
  await page.route(/\/api\/v1\/task-agent\/conversations\/conv-(pinned|scan)$/, async (route) => {
    const conversationId = route.request().url().endsWith("conv-scan") ? "conv-scan" : "conv-pinned";
    if (route.request().method() === "PATCH") {
      const update = route.request().postDataJSON() as { title?: string; pinned?: boolean };
      conversations = conversations.map((conversation) => conversation.conversationId === conversationId
        ? {
            ...conversation,
            ...update,
            pinnedAt: update.pinned === true ? "2026-08-25T08:00:00Z" : update.pinned === false ? null : conversation.pinnedAt,
          }
        : conversation);
      const updated = conversations.find((conversation) => conversation.conversationId === conversationId);
      return route.fulfill({ json: { code: 0, message: "ok", data: updated } });
    }
    return route.fulfill({
      json: {
        code: 0,
        message: "ok",
        data: { conversationId, messages: [], taskId: null },
      },
    });
  });
});

test("会话栏收展使用指定过渡并到达目标宽度", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/agent");

  const layout = page.locator(".task-agent-layout");
  const conversations = page.locator("nav.task-agent-conversations");
  await expect(layout).toHaveCSS("transition-property", "grid-template-columns");
  await expect(layout).toHaveCSS("transition-duration", "0.22s");
  await expect(layout).toHaveCSS("transition-timing-function", "cubic-bezier(0.22, 1, 0.36, 1)");

  await page.getByRole("button", { name: "收起会话栏" }).click();
  await expect.poll(async () => Math.abs(await conversations.evaluate((element) => element.getBoundingClientRect().width) - 56)).toBeLessThan(1);

  await page.getByRole("button", { name: "展开会话栏" }).click();
  await expect.poll(async () => Math.abs(await conversations.evaluate((element) => element.getBoundingClientRect().width) - 260)).toBeLessThan(1);
});

test("减少动态效果设置会禁用会话栏收展过渡", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/agent");

  const layout = page.locator(".task-agent-layout");
  await expect(layout).toHaveCSS("transition-property", "none");
  await expect(layout).toHaveCSS("transition-duration", "0s");
});

test("侧栏不会丢失折叠、标题搜索和带图标管理菜单行为", async ({ page }) => {
  await page.goto("/agent");

  await expect(page.getByText("置顶", { exact: true })).toBeVisible();
  const unpinnedGroup = page.locator(".task-agent-conversation-group", {
    has: page.getByText("昨天", { exact: true }),
  });
  await expect(unpinnedGroup.getByText("S2-045 漏洞验证")).toBeVisible();
  await expect(unpinnedGroup.getByText("生产环境告警研判")).not.toBeVisible();
  await page.getByRole("button", { name: "收起会话栏" }).click();
  await expect(page.locator(".task-agent-layout")).toHaveClass(/sidebar-collapsed/);
  await expect(page.getByRole("button", { name: "展开会话栏" })).toBeVisible();

  await page.getByRole("button", { name: "搜索会话" }).click();
  const dialog = page.getByRole("dialog", { name: "搜索会话" });
  await expect(dialog.getByText("生产环境告警研判")).toBeVisible();
  await dialog.getByRole("textbox").fill("S2-045");
  await expect(dialog.getByText("S2-045 漏洞验证")).toBeVisible();
  await expect(dialog.getByText("生产环境告警研判")).not.toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();

  await page.getByRole("button", { name: "搜索会话" }).click();
  await page.locator(".task-agent-search-overlay").click({ position: { x: 8, y: 8 } });
  await expect(dialog).not.toBeVisible();

  await page.getByRole("button", { name: "搜索会话" }).click();
  const detailResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && response.url().endsWith("/api/v1/task-agent/conversations/conv-scan")
  ));
  await dialog.getByRole("button", { name: /S2-045 漏洞验证/ }).click();
  await detailResponse;
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "搜索会话" }).click();
  await expect(dialog.getByText("生产环境告警研判")).toBeVisible();
  await expect(dialog.getByText("S2-045 漏洞验证")).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "展开会话栏" }).click();
  await page.getByRole("button", { name: "管理 S2-045 漏洞验证" }).click();
  await expect(page.getByRole("button", { name: "重命名" }).locator("svg")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "置顶" }).locator("svg")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "删除" }).locator("svg")).toHaveCount(1);
});

test("管理菜单的键盘操作不会误加载会话或吞掉菜单动作", async ({ page }) => {
  await page.goto("/agent");

  const currentConversation = page.getByRole("button", { name: "生产环境告警研判", exact: true });
  const scanConversation = page.getByRole("button", { name: "S2-045 漏洞验证", exact: true });
  await expect(currentConversation).toHaveClass(/active/);
  await expect(scanConversation).not.toHaveClass(/active/);

  const manageButton = page.getByRole("button", { name: "管理 S2-045 漏洞验证", exact: true });
  await manageButton.focus();
  await page.keyboard.press("Enter");
  const renameButton = page.getByRole("button", { name: "重命名", exact: true });
  await expect(renameButton).toBeVisible();
  await expect(scanConversation).not.toHaveClass(/active/);

  await renameButton.focus();
  await page.keyboard.press("Space");
  await expect(page.locator(".task-agent-rename-input")).toBeVisible();
  await expect(scanConversation).not.toHaveClass(/active/);
});

test("置顶会话不会发送错误更新或停留在日期分组", async ({ page }) => {
  await page.goto("/agent");

  await page.getByRole("button", { name: "管理 S2-045 漏洞验证" }).click();
  const patchRequest = page.waitForRequest((request) => (
    request.method() === "PATCH"
    && request.url().endsWith("/api/v1/task-agent/conversations/conv-scan")
  ));
  await page.getByRole("button", { name: "置顶", exact: true }).click();
  expect((await patchRequest).postDataJSON()).toEqual({ pinned: true });

  const pinnedGroup = page.locator(".task-agent-conversation-group", {
    has: page.getByText("置顶", { exact: true }),
  });
  await expect(pinnedGroup.getByText("生产环境告警研判")).toBeVisible();
  await expect(pinnedGroup.getByText("S2-045 漏洞验证")).toBeVisible();
  await expect(page.locator(".task-agent-conversation-group").first().getByText("置顶", { exact: true })).toBeVisible();
});

test("重命名失败会显示错误并保留当前会话", async ({ page }) => {
  let listRequests = 0;
  await page.route("**/api/v1/task-agent/conversations?limit=50", async (route) => {
    listRequests += 1;
    await route.fallback();
  });
  await page.route("**/api/v1/task-agent/conversations/conv-pinned", async (route) => {
    if (route.request().method() === "PATCH") {
      return route.fulfill({ status: 503, json: { message: "重命名暂不可用" } });
    }
    return route.fallback();
  });

  await page.goto("/agent");

  const currentConversation = page.getByRole("button", { name: "生产环境告警研判", exact: true });
  await expect(currentConversation).toHaveClass(/active/);
  await page.getByRole("button", { name: "管理 生产环境告警研判" }).click();
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  const renameInput = page.locator(".task-agent-rename-input");
  await renameInput.fill("新的会话标题");
  await renameInput.press("Enter");

  await expect(page.locator(".task-agent-conversation-error")).toContainText("会话重命名失败：重命名暂不可用");
  await expect(currentConversation).toHaveClass(/active/);
  await expect(currentConversation).toContainText("生产环境告警研判");
  expect(await page.evaluate(() => localStorage.getItem("trustguard.agent.conversationId"))).toBe("conv-pinned");
  expect(listRequests).toBe(1);
});

test("删除失败会显示错误并保留当前会话", async ({ page }) => {
  let listRequests = 0;
  await page.route("**/api/v1/task-agent/conversations?limit=50", async (route) => {
    listRequests += 1;
    await route.fallback();
  });
  await page.route("**/api/v1/task-agent/conversations/conv-pinned", async (route) => {
    if (route.request().method() === "DELETE") {
      return route.fulfill({ status: 503, json: { message: "删除暂不可用" } });
    }
    return route.fallback();
  });

  await page.goto("/agent");

  const currentConversation = page.getByRole("button", { name: "生产环境告警研判", exact: true });
  await expect(currentConversation).toHaveClass(/active/);
  await page.getByRole("button", { name: "管理 生产环境告警研判" }).click();
  await page.getByRole("button", { name: "删除", exact: true }).click();

  await expect(page.locator(".task-agent-conversation-error")).toContainText("会话删除失败：删除暂不可用");
  await expect(currentConversation).toHaveClass(/active/);
  await expect(currentConversation).toContainText("生产环境告警研判");
  expect(await page.evaluate(() => localStorage.getItem("trustguard.agent.conversationId"))).toBe("conv-pinned");
  expect(listRequests).toBe(1);
});
