import { expect, test } from "@playwright/test";

const completedActivities = [
  {
    id: "activity-collect",
    kind: "analysis",
    title: "收集目标信息",
    detail: "已读取授权范围",
    status: "done",
    timestamp: "2026-08-26T08:00:00Z",
  },
  {
    id: "activity-plan",
    kind: "guard",
    title: "确认执行边界",
    detail: "仅执行授权操作",
    status: "done",
    timestamp: "2026-08-26T08:00:01Z",
  },
];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("sentinel_logged_in_v1", "1");

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      if (!url.includes("/api/v1/task-agent/draft/stream")) {
        return originalFetch(input, init);
      }

      const encoder = new TextEncoder();
      (window as Window & { __taskAgentDraftEncoder?: TextEncoder }).__taskAgentDraftEncoder = encoder;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          (window as Window & { __taskAgentDraftController?: ReadableStreamDefaultController<Uint8Array> })
            .__taskAgentDraftController = controller;
          const signal = init?.signal;
          signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          }, { once: true });
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    };
  });

  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/v1/tasks", async (route) => {
    await route.fulfill({ json: { code: 0, message: "ok", data: [] } });
  });
  await page.route("**/api/v1/task-agent/conversations?limit=50", async (route) => {
    await route.fulfill({
      json: {
        code: 0,
        message: "ok",
        data: [{
          conversationId: "conv-history",
          title: "授权范围检查",
          preview: "已完成",
          messageCount: 2,
          createdAt: "2026-08-26T08:00:00Z",
          updatedAt: "2026-08-26T08:00:03Z",
          pinned: false,
          pinnedAt: null,
        }],
      },
    });
  });
  await page.route("**/api/v1/task-agent/conversations/conv-history", async (route) => {
    await route.fulfill({
      json: {
        code: 0,
        message: "ok",
        data: {
          conversationId: "conv-history",
          taskId: null,
          messages: [
            {
              id: "history-user",
              role: "user",
              text: "检查授权范围",
              activities: [],
              createdAt: "2026-08-26T08:00:00Z",
            },
            {
              id: "history-assistant",
              role: "assistant",
              text: "已完成授权范围检查。",
              activities: completedActivities,
              createdAt: "2026-08-26T08:00:03Z",
            },
          ],
        },
      },
    });
  });
});

test("恢复的 Assistant 步骤摘要默认折叠，并可展开后再次收起", async ({ page }) => {
  await page.goto("/agent");

  const conversation = page.getByLabel("对话");
  const summary = conversation.getByRole("button", { name: /已完成 2 个步骤/ });
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(summary.locator(".tg-toolcall-title small")).toHaveCount(0);
  const details = conversation.locator(".tg-toolcall-body-shell");
  const activityDetail = conversation.getByText("已读取授权范围");
  await expect(details).toHaveAttribute("aria-hidden", "true");
  await expect(activityDetail).not.toBeVisible();

  await summary.click();
  await expect(summary).toHaveAttribute("aria-expanded", "true");
  await expect(details).toHaveAttribute("aria-hidden", "false");
  await expect(activityDetail).toBeVisible();

  await summary.click();
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(activityDetail).not.toBeVisible();
});

test("停止生成会终结运行活动并显示停止摘要", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("textbox").fill("请创建测试草稿");
  await page.getByRole("textbox").press("Enter");
  await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __taskAgentDraftController?: unknown }).__taskAgentDraftController))).toBe(true);

  await page.evaluate(() => {
    const state = window as Window & {
      __taskAgentDraftController: ReadableStreamDefaultController<Uint8Array>;
      __taskAgentDraftEncoder: TextEncoder;
    };
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      [
        'event: activity\ndata: {"id":"step-1","kind":"analysis","title":"理解任务意图","detail":"已识别目标、测试重点和风险偏好","status":"done","timestamp":"2026-08-26T08:01:00Z"}\n\n',
        'event: activity\ndata: {"id":"step-2","kind":"guard","title":"检查目标与安全边界","detail":"已完成目标格式、授权范围与风险边界检查","status":"done","timestamp":"2026-08-26T08:01:01Z"}\n\n',
        'event: activity\ndata: {"id":"step-3","kind":"result","title":"生成任务草稿","detail":"草稿已准备好，等待确认","status":"done","timestamp":"2026-08-26T08:01:02Z"}\n\n',
      ].join(''),
    ));
  });
  await expect(page.getByLabel("对话").getByText("草稿已准备好，等待确认", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "停止生成" }).click();

  const conversation = page.getByLabel("对话");
  const summary = conversation.getByRole("button", { name: "步骤执行已停止" });
  await expect(conversation.getByText("已停止生成。", { exact: true })).toBeVisible();
  await expect(summary).toBeVisible();
  await expect(summary.locator(".tg-toolcall-icon")).toHaveClass(/blocked/);
  await expect(summary.locator(".tg-toolcall-icon")).toHaveText("×");
  await expect(conversation.locator(".tg-toolcall-item.running")).toHaveCount(0);
  await expect(conversation.getByText("正在执行：检查授权范围", { exact: true })).toHaveCount(0);
});

test("非中止流错误会通过相同路径终结运行活动", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("textbox").fill("请创建测试草稿");
  await page.getByRole("textbox").press("Enter");
  await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __taskAgentDraftController?: unknown }).__taskAgentDraftController))).toBe(true);

  await page.evaluate(() => {
    const state = window as Window & {
      __taskAgentDraftController: ReadableStreamDefaultController<Uint8Array>;
      __taskAgentDraftEncoder: TextEncoder;
    };
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      [
        'event: activity\ndata: {"id":"step-1","kind":"analysis","title":"理解任务意图","detail":"已识别目标、测试重点和风险偏好","status":"done","timestamp":"2026-08-26T08:01:00Z"}\n\n',
        'event: activity\ndata: {"id":"step-2","kind":"guard","title":"检查目标与安全边界","detail":"已完成目标格式、授权范围与风险边界检查","status":"done","timestamp":"2026-08-26T08:01:01Z"}\n\n',
        'event: activity\ndata: {"id":"step-3","kind":"result","title":"生成任务草稿","detail":"草稿已准备好，等待确认","status":"done","timestamp":"2026-08-26T08:01:02Z"}\n\n',
      ].join(''),
    ));
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      'event: error\ndata: {"message":"上游连接断开"}\n\n',
    ));
  });

  const conversation = page.getByLabel("对话");
  await expect(conversation.getByText("请求失败：上游连接断开", { exact: true })).toBeVisible();
  await expect(conversation.getByRole("button", { name: "步骤执行已停止" })).toBeVisible();
  await expect(conversation.locator(".tg-toolcall-item.running")).toHaveCount(0);
});

test("草稿步骤与首段正文同批到达时仍会先展示处理过程", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("textbox").fill("请创建测试草稿");
  await page.getByRole("textbox").press("Enter");

  await expect(page.getByText("正在理解任务", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __taskAgentDraftController?: unknown }).__taskAgentDraftController))).toBe(true);

  await page.evaluate(() => {
    const state = window as Window & {
      __taskAgentDraftController: ReadableStreamDefaultController<Uint8Array>;
      __taskAgentDraftEncoder: TextEncoder;
    };
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      [
        'event: activity\ndata: {"id":"step-1","kind":"analysis","title":"理解任务意图","detail":"正在提取目标与测试要求","status":"running","timestamp":"2026-08-26T08:01:00Z"}\n\n',
        'event: activity\ndata: {"id":"step-1","kind":"analysis","title":"理解任务意图","detail":"已识别目标、测试重点和风险偏好","status":"done","timestamp":"2026-08-26T08:01:00Z"}\n\n',
        'event: activity\ndata: {"id":"step-2","kind":"guard","title":"检查目标与安全边界","detail":"正在验证目标格式与授权边界","status":"running","timestamp":"2026-08-26T08:01:01Z"}\n\n',
        'event: activity\ndata: {"id":"step-2","kind":"guard","title":"检查目标与安全边界","detail":"已完成目标格式、授权范围与风险边界检查","status":"done","timestamp":"2026-08-26T08:01:01Z"}\n\n',
        'event: activity\ndata: {"id":"step-3","kind":"result","title":"生成任务草稿","detail":"正在整理可确认的任务草稿","status":"running","timestamp":"2026-08-26T08:01:02Z"}\n\n',
        'event: activity\ndata: {"id":"step-3","kind":"result","title":"生成任务草稿","detail":"草稿已准备好，等待确认","status":"done","timestamp":"2026-08-26T08:01:02Z"}\n\n',
        'event: delta\ndata: {"text":"已读取授权范围。"}\n\n',
      ].join(''),
    ));
  });

  const conversation = page.getByLabel("对话");
  await expect(conversation.getByText("已读取授权范围。", { exact: true })).toBeVisible();
  await expect(conversation.getByText("理解任务意图", { exact: true })).toBeVisible();
  await expect(conversation.getByText("检查目标与安全边界", { exact: true })).toBeVisible();
  await expect(conversation.getByText("生成任务草稿", { exact: true })).toBeVisible();
  await expect(conversation.getByText("草稿已准备好，等待确认", { exact: true })).toBeVisible();
  await expect(page.locator(".tg-thinking")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "已完成 3 个步骤" })).toBeVisible();

  await page.evaluate(() => {
    const state = window as Window & {
      __taskAgentDraftController: ReadableStreamDefaultController<Uint8Array>;
      __taskAgentDraftEncoder: TextEncoder;
    };
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      'event: result\ndata: {"status":"READY","conversationId":"conv-stream","missingFields":[],"warnings":[],"assistantMessage":"草稿已生成。","activities":[{"id":"step-1","kind":"analysis","title":"理解任务意图","detail":"已识别目标、测试重点和风险偏好","status":"done","timestamp":"2026-08-26T08:01:00Z"},{"id":"step-2","kind":"guard","title":"检查目标与安全边界","detail":"已完成目标格式、授权范围与风险边界检查","status":"done","timestamp":"2026-08-26T08:01:01Z"},{"id":"step-3","kind":"result","title":"生成任务草稿","detail":"草稿已准备好，等待确认","status":"done","timestamp":"2026-08-26T08:01:02Z"}],"workflowId":"pentest"}\n\n',
    ));
    state.__taskAgentDraftController.close();
  });
  const summary = page.getByRole("button", { name: /已完成 3 个步骤/ });
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(conversation.locator(".tg-toolcall-body-shell")).toHaveAttribute("aria-hidden", "true");
  await expect(conversation.getByText("草稿已准备好，等待确认", { exact: true })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
});

test("多行输入在生成期间显示上下文提示，完成后恢复高度", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "新建会话" }).click();

  const composer = page.locator(".task-agent-composer textarea");
  const initialHeight = await composer.evaluate((element) => element.getBoundingClientRect().height);
  await composer.fill(["检查目标", "确认范围", "收集证据", "生成草稿"].join("\n"));

  const expandedHeight = await composer.evaluate((element) => element.getBoundingClientRect().height);
  expect(expandedHeight).toBeGreaterThan(initialHeight);
  expect(expandedHeight).toBeLessThanOrEqual(130);

  await composer.fill(Array.from({ length: 16 }, (_, index) => `第 ${index + 1} 行输入`).join("\n"));
  const cappedHeight = await composer.evaluate((element) => ({
    inlineHeight: element.style.height,
    computedHeight: getComputedStyle(element).height,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  expect(cappedHeight.inlineHeight).toBe("130px");
  expect(cappedHeight.computedHeight).toBe("130px");
  expect(cappedHeight.scrollHeight).toBeGreaterThan(cappedHeight.clientHeight);

  await composer.press("Enter");
  await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  await expect(page.getByText("正在生成，点击停止按钮可中断", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __taskAgentDraftController?: unknown }).__taskAgentDraftController))).toBe(true);

  await page.evaluate(() => {
    const state = window as Window & {
      __taskAgentDraftController: ReadableStreamDefaultController<Uint8Array>;
      __taskAgentDraftEncoder: TextEncoder;
    };
    state.__taskAgentDraftController.enqueue(state.__taskAgentDraftEncoder.encode(
      'event: result\ndata: {"status":"READY","conversationId":"conv-input","missingFields":[],"warnings":[],"assistantMessage":"草稿已生成。","activities":[],"workflowId":"pentest"}\n\n',
    ));
    state.__taskAgentDraftController.close();
  });

  await expect(composer).toHaveValue("");
  await expect.poll(() => composer.evaluate((element) => element.getBoundingClientRect().height)).toBe(initialHeight);
});

test("默认动态效果为消息、步骤和搜索弹窗提供短入场反馈", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/agent");

  const message = page.locator(".task-agent-message").first();
  await expect(message).toHaveCSS("animation-duration", "0.16s");
  expect(await message.evaluate((element) => getComputedStyle(element).animationName)).not.toBe("none");

  const details = page.locator(".tg-toolcall-body-shell");
  const detailTransition = await details.evaluate((element) => {
    const styles = getComputedStyle(element);
    const values = (value: string) => value.split(",").map((item) => item.trim());
    const properties = values(styles.transitionProperty);
    const durations = values(styles.transitionDuration);
    const delays = values(styles.transitionDelay);
    const transition = (property: string) => {
      const index = properties.indexOf(property);
      return {
        duration: durations[index % durations.length],
        delay: delays[index % delays.length],
      };
    };
    return {
      property: styles.transitionProperty,
      gridRows: transition("grid-template-rows"),
      opacity: transition("opacity"),
      visibility: transition("visibility"),
    };
  });
  expect(detailTransition.property).toContain("grid-template-rows");
  expect(detailTransition.property).toContain("opacity");
  expect(detailTransition.property).toContain("visibility");
  expect(detailTransition.gridRows.duration).toBe("0.18s");
  expect(detailTransition.opacity.duration).toBe("0.18s");
  expect(detailTransition.visibility).toEqual({ duration: "0s", delay: "0.18s" });

  const summary = page.getByRole("button", { name: /已完成 2 个步骤/ });
  await summary.click();
  await expect(details).toHaveAttribute("aria-hidden", "false");
  await expect(page.getByLabel("对话").getByText("已读取授权范围")).toBeVisible();
  const expandedVisibilityTransition = await details.evaluate((element) => {
    const styles = getComputedStyle(element);
    const properties = styles.transitionProperty.split(",").map((item) => item.trim());
    const delays = styles.transitionDelay.split(",").map((item) => item.trim());
    return delays[properties.indexOf("visibility") % delays.length];
  });
  expect(expandedVisibilityTransition).toBe("0s");

  await summary.click();
  await expect(details).toHaveAttribute("aria-hidden", "true");
  await expect(page.getByLabel("对话").getByText("已读取授权范围")).not.toBeVisible();

  await page.getByRole("button", { name: "搜索会话" }).click();
  const overlay = page.locator(".task-agent-search-overlay");
  const dialog = page.locator(".task-agent-search-dialog");
  await expect(overlay).toHaveCSS("animation-duration", "0.16s");
  await expect(dialog).toHaveCSS("animation-duration", "0.16s");
  expect(await overlay.evaluate((element) => getComputedStyle(element).animationName)).not.toBe("none");
  expect(await dialog.evaluate((element) => getComputedStyle(element).animationName)).not.toBe("none");
});

test("减少动态效果设置会关闭对话和搜索的新增动效", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/agent");

  const message = page.locator(".task-agent-message").first();
  await expect(message).toHaveCSS("animation-name", "none");
  await expect(message).toHaveCSS("animation-duration", "0s");

  const details = page.locator(".tg-toolcall-body-shell");
  await expect(details).toHaveCSS("transition-property", "none");
  await expect(details).toHaveCSS("transition-duration", "0s");

  await page.getByRole("button", { name: "搜索会话" }).click();
  const overlay = page.locator(".task-agent-search-overlay");
  const dialog = page.locator(".task-agent-search-dialog");
  await expect(overlay).toHaveCSS("animation-name", "none");
  await expect(overlay).toHaveCSS("animation-duration", "0s");
  await expect(dialog).toHaveCSS("animation-name", "none");
  await expect(dialog).toHaveCSS("animation-duration", "0s");
});
