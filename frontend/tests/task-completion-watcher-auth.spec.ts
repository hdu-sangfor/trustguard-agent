import { expect, test } from "@playwright/test";

test("未登录页面不会轮询受保护的任务接口", async ({ page }) => {
  let taskRequests = 0;

  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/v1/tasks", async (route) => {
    taskRequests += 1;
    await route.fulfill({ status: 401, json: { message: "未授权" } });
  });

  await page.goto("/");
  await page.waitForTimeout(700);

  expect(taskRequests).toBe(0);
});
