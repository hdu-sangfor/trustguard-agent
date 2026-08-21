import { expect, test } from "@playwright/test";

test("首页同时向评委说明项目价值并引导用户开始任务", async ({ page }) => {
  await page.goto("/");
  const hero = page.getByLabel("让每一次安全测试，都有计划、有边界、有证据。");

  await expect(page.getByRole("heading", { name: /让每一次安全测试.*都有计划.*有边界.*有证据/ })).toBeVisible();
  await expect(hero.getByRole("link", { name: "开始安全任务" })).toHaveAttribute("href", "/agent");

  await hero.getByRole("link", { name: "查看工作流程" }).click();
  await expect(page.getByRole("region", { name: "可信安全任务工作流程" })).toBeInViewport();
});

test("Hero 顶部能力索引只展示不跳转", async ({ page }) => {
  await page.goto("/");

  const quickIndex = page.getByLabel("首页能力快捷入口");
  await expect(quickIndex).toBeVisible();
  await expect(quickIndex.getByRole("link")).toHaveCount(0);
  await expect(quickIndex.getByText("任务编排", { exact: true })).toBeVisible();
  await expect(quickIndex.getByText("技能执行", { exact: true })).toBeVisible();
  await expect(quickIndex.getByText("证据链", { exact: true })).toBeVisible();
  await expect(quickIndex.getByText("知识增强", { exact: true })).toBeVisible();
});

test("首页沿用产品菜单而不显示独立落地页菜单", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator('[data-cmp="Header"]')).toBeVisible();
  await expect(page.locator(".landing-header")).toHaveCount(0);
});

test("首页沿用原产品的浅色背景和蓝色强调色", async ({ page }) => {
  await page.goto("/");
  const hero = page.getByLabel("让每一次安全测试，都有计划、有边界、有证据。");

  await expect(page.locator(".landing-page")).toHaveCSS("background-color", "rgb(244, 247, 251)");
  await expect(hero.getByRole("link", { name: "开始安全任务" })).toHaveCSS("background-color", "rgb(3, 105, 161)");
});

test("移动端主标题的三条语义不会被再次拆行", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const lines = page.locator("#landing-title > span");

  await expect(lines).toHaveCount(3);
  const wraps = await lines.evaluateAll((elements) => elements.map((element) => {
    const style = getComputedStyle(element);
    return element.getBoundingClientRect().height > Number.parseFloat(style.lineHeight) * 1.2;
  }));
  expect(wraps).toEqual([false, false, false]);
});

test("首页把四类平台能力连接到现有产品页面", async ({ page }) => {
  await page.goto("/");
  const domains = page.getByRole("region", { name: "TrustGuard 四大能力" });
  await domains.scrollIntoViewIfNeeded();

  await expect(domains.getByRole("heading", { name: "Task Agent" })).toBeVisible();
  await expect(domains.getByText(/30\+ 安全技能、专用能力定义和漏洞验证模板/)).toBeVisible();
  await expect(domains.getByText(/接入 RAG 检索.*计划、执行与证据判断/)).toBeVisible();
  await expect(domains.getByRole("link", { name: /进入可信卫士/ })).toHaveAttribute("href", "/agent");
  await expect(domains.getByRole("link", { name: /查看技能库/ })).toHaveAttribute("href", "/skills");
  await expect(domains.getByRole("link", { name: /查看报告中心/ })).toHaveAttribute("href", "/reports");
  await expect(domains.getByRole("link", { name: /进入知识中心/ })).toHaveAttribute("href", "/knowledge");
});

test("首页移除 Hero 下方三步条和独立痛点段", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator(".landing-evidence-flow")).toHaveCount(0);
  await expect(page.locator(".landing-truth")).toHaveCount(0);
  await expect(page.getByLabel("从用户意图到安全证据的三个步骤")).toHaveCount(0);
  await expect(page.getByText("THE UNCOMFORTABLE TRUTH")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /30\+ 个安全工具.*不等于一条可信的安全能力/ })).toHaveCount(0);
});

test("滚轮推进时会在六阶段安全流程中切换", async ({ page }) => {
  await page.goto("/");
  const workflow = page.getByRole("region", { name: "可信安全任务工作流程" });

  await expect(workflow).toHaveAttribute("data-active-step", "1");
  await expect(workflow.getByRole("listitem")).toHaveCount(6);
  await expect(workflow.getByRole("heading", { name: "资产侦察" })).toBeVisible();

  const bounds = await workflow.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top + window.scrollY, height: rect.height };
  });
  await page.evaluate(({ top, height }) => {
    window.scrollTo(0, top + (height - window.innerHeight) * 0.52);
  }, bounds);

  await expect(workflow).toHaveAttribute("data-active-step", "4");
  await expect(workflow.locator(".landing-workflow-stage")).toBeInViewport();
  await expect(workflow.getByRole("heading", { name: "授权验证" })).toBeVisible();
});

test("快速滚动流程时不会出现多个阶段文字叠加", async ({ page }) => {
  await page.goto("/");
  const workflow = page.getByRole("region", { name: "可信安全任务工作流程" });

  const bounds = await workflow.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top + window.scrollY, height: rect.height };
  });
  const maxVisibleScenes = await page.evaluate(async ({ top, height }) => {
    let maxVisible = 0;
    const positions = [0.12, 0.72, 0.28, 0.9, 0.5];
    for (const progress of positions) {
      window.scrollTo(0, top + (height - window.innerHeight) * progress);
      await new Promise((resolve) => window.setTimeout(resolve, 70));
      const visible = Array.from(document.querySelectorAll<HTMLElement>(".landing-workflow-scene")).filter((scene) => {
        const style = window.getComputedStyle(scene);
        return style.visibility !== "hidden" && Number(style.opacity) > 0.01;
      }).length;
      maxVisible = Math.max(maxVisible, visible);
    }
    return maxVisible;
  }, bounds);

  expect(maxVisibleScenes).toBe(1);
});

test("首页不展示真实任务证据区", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /让每一次安全测试.*都有计划.*有边界.*有证据/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "真实任务证据" })).toHaveCount(0);
  await expect(page.locator(".landing-proof")).toHaveCount(0);
});

test("首页保留 Agent、RAG、流程和技术架构主线", async ({ page }) => {
  await page.goto("/");
  const domains = page.getByRole("region", { name: "TrustGuard 四大能力" });
  const workflow = page.getByRole("region", { name: "可信安全任务工作流程" });
  const architecture = page.getByRole("region", { name: "TrustGuard 技术架构" });

  await domains.scrollIntoViewIfNeeded();
  await expect(domains.getByRole("heading", { name: "Task Agent" })).toBeVisible();
  await expect(domains.getByRole("heading", { name: "Knowledge Intelligence" })).toBeVisible();
  await expect(domains.getByText("知识增强与经验沉淀")).toBeVisible();

  await workflow.scrollIntoViewIfNeeded();
  await expect(workflow.getByLabel("六阶段安全工作流程").getByText("资产侦察")).toBeVisible();
  await expect(workflow.getByLabel("六阶段安全工作流程").getByText("威胁建模")).toBeVisible();
  await expect(workflow.getByLabel("六阶段安全工作流程").getByText("证据报告")).toBeVisible();

  await architecture.scrollIntoViewIfNeeded();

  await expect(architecture.getByText("Gateway", { exact: true })).toBeVisible();
  await expect(architecture.getByText("Supervisor", { exact: true })).toBeVisible();
  await expect(architecture.getByText("Orchestrator", { exact: true })).toBeVisible();
  await expect(architecture.getByText("Executor", { exact: true })).toBeVisible();
  await expect(architecture.getByText("Evidence / RAG", { exact: true })).toBeVisible();
});

test("首页删去 06 和 07 页面", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /让每一次安全测试.*都有计划.*有边界.*有证据/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "深入了解 TrustGuard" })).toHaveCount(0);
  await expect(page.locator(".landing-explore")).toHaveCount(0);
  await expect(page.getByText("EXPLORE THE SYSTEM")).toHaveCount(0);
  await expect(page.getByText("START WITH A REAL TASK")).toHaveCount(0);
});

test("首页末尾提供立即体验入口", async ({ page }) => {
  await page.goto("/");
  const experience = page.getByRole("region", { name: "立即体验 TrustGuard" });
  await experience.scrollIntoViewIfNeeded();

  await expect(experience.getByText("06")).toBeVisible();
  await expect(experience.getByRole("heading", { name: "立即体验可信安全 Agent" })).toBeVisible();
  await expect(experience.getByText(/生成计划.*调用技能.*判断证据.*安全报告/)).toBeVisible();
  await expect(experience.getByRole("link", { name: /开始安全任务/ })).toHaveAttribute("href", "/agent");
  await expect(experience.getByRole("link", { name: /查看技术特点/ })).toHaveAttribute("href", "/features");
});

test("减少动态效果时桌面布局不会误切换为移动端", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  const cards = page.locator(".landing-domain-card");
  const first = await cards.nth(0).boundingBox();
  const second = await cards.nth(1).boundingBox();
  expect(first).not.toBeNull();
  expect(second).not.toBeNull();
  expect(Math.abs((first?.y ?? 0) - (second?.y ?? 0))).toBeLessThan(4);
  expect((second?.x ?? 0)).toBeGreaterThan((first?.x ?? 0));
  await expect(page.getByLabel("六阶段安全工作流程")).toBeVisible();
});

test("移动端工作流程默认只展开一个阶段并可切换", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const workflow = page.getByRole("region", { name: "可信安全任务工作流程" });
  await expect(workflow.getByRole("button", { name: /01.*资产侦察/ })).toHaveAttribute("aria-expanded", "true");
  await expect(workflow.getByRole("button", { name: /02.*威胁建模/ })).toHaveAttribute("aria-expanded", "false");
  await expect(workflow.getByText("识别授权范围内的目标资产", { exact: false })).toBeVisible();
  await workflow.getByRole("button", { name: /02.*威胁建模/ }).click();
  await expect(workflow.getByRole("button", { name: /01.*资产侦察/ })).toHaveAttribute("aria-expanded", "false");
  await expect(workflow.getByRole("button", { name: /02.*威胁建模/ })).toHaveAttribute("aria-expanded", "true");
  await expect(workflow.getByText("明确下一阶段需要验证的问题", { exact: false })).toBeVisible();
});
