import { expect, test } from "@playwright/test";

test("技术特点页优先解释产品能力并提供操作入口", async ({ page }) => {
  await page.goto("/features");

  await expect(page.getByRole("heading", { name: "技术特点", exact: true })).toBeVisible();
  await expect(page.getByText(/任务级授权.*动态编排.*隔离技能执行.*证据链追踪/)).toBeVisible();

  const overview = page.getByRole("region", { name: "技术能力概览" });
  await expect(overview.getByText("动态编排", { exact: true })).toBeVisible();
  await expect(overview.getByText("隔离执行", { exact: true })).toBeVisible();
  await expect(overview.getByText("证据追踪", { exact: true })).toBeVisible();
  await expect(overview.getByText("知识增强", { exact: true })).toBeVisible();
  await expect(overview.getByText("LangGraph", { exact: true })).toBeVisible();
  await expect(overview.getByText("Containerized Skills", { exact: true })).toBeVisible();
  await expect(overview.getByText("Evidence Trace", { exact: true })).toBeVisible();
  await expect(overview.getByText("RAG", { exact: true })).toBeVisible();
  await expect(page.getByText(/RAG.*可选/)).toHaveCount(0);

  await expect(page.getByRole("link", { name: "开始安全任务" })).toHaveAttribute("href", "/agent");
  await expect(page.getByRole("link", { name: "查看技能库" })).toHaveAttribute("href", "/skills");
  await expect(page.getByRole("link", { name: "系统状态" })).toHaveAttribute("href", "/system");
});

test("技术流程包含监督、执行、知识增强和证据闭环", async ({ page }) => {
  await page.goto("/features");

  const flow = page.getByRole("img", { name: "TrustGuard 技术流程图" });
  await expect(flow).toContainText("Gateway");
  await expect(flow).toContainText("Supervisor");
  await expect(flow).toContainText("Orchestrator");
  await expect(flow).toContainText("Executor");
  await expect(flow).toContainText("RAG");
  await expect(flow).toContainText("Evidence");
  await expect(flow).toContainText("报告输出");
});

test("核心技术特点面向产品价值，开发对象默认收起", async ({ page }) => {
  await page.goto("/features");

  const core = page.getByRole("region", { name: "核心技术特点" });
  for (const title of ["授权范围控制", "动态任务编排", "隔离技能执行", "证据优先", "人工确认", "知识增强"]) {
    await expect(core.getByRole("heading", { name: title })).toBeVisible();
  }

  const reference = page.getByText("开发者实现参考", { exact: true });
  await expect(reference).toBeVisible();
  await expect(page.getByText("ApiExecutionRecord", { exact: true })).not.toBeVisible();
  await reference.click();
  await expect(page.getByText("ApiExecutionRecord", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "当前部署状态" })).toHaveCount(0);
});

test("技术特点页移动端不横向溢出且正文保持可读", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/features");

  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  const descriptionSize = await page.locator(".features-section-heading p").first().evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).fontSize));
  expect(descriptionSize).toBeGreaterThanOrEqual(13);
});
