import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("sentinel_logged_in_v1", "1");
    localStorage.setItem("sentinel_orbit_tasks_v1", JSON.stringify([{
      id: "task-1",
      name: "S2-045 漏洞验证",
      desc: "旧描述",
      url: "http://s2-045-target:8080/",
      log: "保留已有日志",
      createdAt: Date.parse("2026-08-26T08:00:00Z"),
      updatedAt: Date.parse("2026-08-26T08:01:00Z"),
      status: "running",
      currentPhase: "EXPLOIT",
    }]));
  });

  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/v1/task-agent/conversations?limit=50", async (route) => {
    await route.fulfill({ json: { code: 0, message: "ok", data: [] } });
  });
  await page.route("**/api/v1/tasks", async (route) => {
    await route.fulfill({
      json: {
        code: 0,
        message: "ok",
        data: [{
          id: 1,
          taskId: "task-1",
          name: "S2-045 漏洞验证",
          target: "http://s2-045-target:8080/",
          description: "后端描述",
          status: "DONE",
          currentPhase: "DONE",
          createdAt: "2026-08-26T08:00:00Z",
          updatedAt: "2026-08-26T08:05:00Z",
        }],
      },
    });
  });
});

test("全局任务提醒会用后端状态更新已有本地任务", async ({ page }) => {
  await page.goto("/agent");

  await expect.poll(async () => page.evaluate(() => {
    const raw = localStorage.getItem("sentinel_orbit_tasks_v1");
    const tasks = raw ? JSON.parse(raw) as Array<{ id: string; status: string; log?: string }> : [];
    return tasks.find((task) => task.id === "task-1");
  })).toMatchObject({
    id: "task-1",
    status: "finished",
    log: "保留已有日志",
  });
  await expect(page.getByText("任务完成：S2-045 漏洞验证")).toBeVisible();
});
