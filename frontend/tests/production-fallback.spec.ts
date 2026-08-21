import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("sentinel_logged_in_v1", "1");
  });
});

test("生产模式的真实统计不会混入演示漏洞", async ({ page }) => {
  await page.route("**/api/v1/admin/analytics/overview", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        code: "0",
        data: {
          task_stats: { total: 3, pending: 0, running: 1, paused: 0, done: 2, failed: 0, cancelled: 0 },
          completion_rate: 2 / 3,
          recent_events_count: 5,
          event_type_breakdown: { TASK_COMPLETED: 2 },
          skill_execution_breakdown: { nmap_scan: 3 },
          total_executions: 3,
          total_plans: 2,
          generated_at: "2026-08-20T00:00:00Z",
        },
      }),
    });
  });

  await page.goto("/stats");

  await expect(page.getByText("累计任务")).toBeVisible();
  await expect(page.getByText("CVE-2017-5638")).toHaveCount(0);
  await expect(page.getByText("暂无后端漏洞聚合数据")).toBeVisible();
});

test("生产模式接口失败时不会启用演示任务", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    await route.fulfill({ status: 503, body: "service unavailable" });
  });

  await page.goto("/trace/prod-offline-task");

  await expect(page.getByText("任务数据加载失败，请检查后端连接后重试。")).toBeVisible();
  await expect(page.getByText("演示模式")).toHaveCount(0);
});
