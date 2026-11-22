"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { CheckIcon, PlugIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import {
  modelConnectionsApi,
  type ModelConnection,
  type ModelSettings,
  type ConnectionTestResult,
} from "@/lib/model-connections";
import { getActorEpoch } from "@/lib/auth-session";
import { useAuthStore } from "@/stores/auth-store";
import { useLanguage } from "@/providers/language-provider";
import { isApiError } from "@/lib/api-client";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./ui/dialog";
import { ProviderMaintenance } from "./formal-provider-maintenance";

const initial: ModelSettings = {
  provider: "deepseek",
  model: "",
  pricing_mode: "off",
  input_per_million: null,
  output_per_million: null,
  currency: "USD",
  output_length_mode: "auto",
  max_output_tokens: null,
  max_input_bytes: 32000,
  timeout_ms: 45000,
  price_ceiling_confirmed: false,
  billing_limit_acknowledged: false,
};

export function ModelConnectionsPanel({
  onChanged,
}: { onChanged?: () => Promise<unknown> } = {}) {
  const epoch = useAuthStore((state) => state.actorEpoch);
  return <Connections key={epoch} onChanged={onChanged} />;
}

function Connections({ onChanged }: { onChanged?: () => Promise<unknown> }) {
  const router = useRouter();
  const client = useQueryClient();
  const { locale } = useLanguage();
  const copy = useCallback(
    (zh: string, en: string) => (locale === "zh-CN" ? zh : en),
    [locale],
  );
  const catalog = useQuery({
    queryKey: ["model-connections", getActorEpoch()],
    queryFn: ({ signal }) => modelConnectionsApi.list(signal),
    retry: false,
  });
  const [selection, setSelection] = useState<string | null>(null);
  const [form, setForm] = useState<ModelSettings>(initial);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [manualOutput, setManualOutput] = useState(false);
  const [message, setMessage] = useState("");
  const [testing, setTesting] = useState(false);
  const [connectionTest, setConnectionTest] =
    useState<ConnectionTestResult | null>(null);
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [pendingRoute, setPendingRoute] = useState<string | null>(null);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [inspected, setInspected] = useState("");
  const [extra, setExtra] = useState<ModelConnection[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const keyInput = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const items = useMemo(
    () =>
      [...(catalog.data?.connections ?? []), ...extra].filter(
        (item, index, all) =>
          all.findIndex((row) => row.id === item.id) === index,
      ),
    [catalog.data?.connections, extra],
  );
  const selected = items.find((item) => item.id === selection);
  const profile = catalog.data?.profiles.find(
    (item) => item.id === form.provider,
  );
  const moreOffset =
    nextOffset === -1
      ? null
      : (nextOffset ?? catalog.data?.next_offset ?? null);

  useEffect(() => {
    alive.current = true;
    const node = keyInput.current;
    return () => {
      alive.current = false;
      controller.current?.abort();
      if (node) node.value = "";
    };
  }, []);
  const choose = useCallback(
    (id: string) => {
      const item = items.find((value) => value.id === id);
      setSelection(id);
      setForm(
        item
          ? {
              ...item.settings,
              price_ceiling_confirmed: false,
              billing_limit_acknowledged: false,
            }
          : { ...initial },
      );
      revision.current = catalog.data?.revision ?? 0;
      if (keyInput.current) keyInput.current.value = "";
      setDirty(false);
      setManualOutput(item?.settings.output_length_mode === "manual");
      setMessage("");
      setConnectionTest(null);
      setInspected("");
      setAdvanced(false);
      setPendingSelection(null);
    },
    [items, catalog.data?.revision],
  );
  useEffect(() => {
    if (catalog.data && selection === null)
      choose(
        catalog.data.connections.find((item) => item.is_current)?.id ??
          catalog.data.connections[0]?.id ??
          "",
      );
  }, [catalog.data, selection, choose]);
  useEffect(() => {
    if (
      catalog.data &&
      selection &&
      !dirty &&
      !busy &&
      !items.some((item) => item.id === selection)
    )
      setSelection(null);
  }, [catalog.data, selection, dirty, busy, items]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const intercept = (event: MouseEvent) => {
      if (
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const anchor =
        event.target instanceof Element ? event.target.closest("a") : null;
      if (
        !anchor ||
        anchor.hasAttribute("download") ||
        anchor.target === "_blank"
      )
        return;
      const url = new URL(anchor.href, window.location.href);
      if (
        url.origin !== window.location.origin ||
        url.protocol !== window.location.protocol ||
        (url.pathname === window.location.pathname &&
          url.search === window.location.search)
      )
        return;
      event.preventDefault();
      if (busy)
        setMessage(
          copy(
            "连接正在处理中，请完成后再离开。",
            "The connection is being processed. Wait before leaving.",
          ),
        );
      else setPendingRoute(url.pathname + url.search + url.hash);
    };
    document.addEventListener("click", intercept, true);
    return () => document.removeEventListener("click", intercept, true);
  }, [dirty, busy, copy]);
  function switchTo(id: string) {
    if (busy || id === selection) return;
    if (dirty) setPendingSelection(id);
    else choose(id);
  }
  function change(patch: Partial<ModelSettings>) {
    if (patch.provider && patch.provider !== form.provider && keyInput.current)
      keyInput.current.value = "";
    setForm({ ...form, ...patch });
    setDirty(true);
    setMessage("");
    setConnectionTest(null);
    setInspected("");
  }
  async function refresh() {
    setExtra([]);
    setNextOffset(null);
    void client.invalidateQueries({ queryKey: ["research", "provider"] });
    const data = (await catalog.refetch({ throwOnError: true })).data;
    await onChanged?.();
    return data;
  }
  async function perform<T>(
    work: (signal: AbortSignal) => Promise<T>,
    accepted: (result: T) => Promise<void> | void,
    failed?: (error: unknown) => string,
  ) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setMessage("");
    const abort = new AbortController();
    controller.current = abort;
    const epoch = getActorEpoch();
    try {
      const result = await work(abort.signal);
      if (alive.current && epoch === getActorEpoch() && !abort.signal.aborted)
        await accepted(result);
    } catch (error) {
      if (alive.current && !abort.signal.aborted) {
        const code = isApiError(error) ? error.errorCode : "";
        setMessage(
          failed
            ? failed(error)
            : code.endsWith("CREDENTIAL_IN_USE")
              ? copy(
                  "连接正在用于运行，请在运行结束后移除。",
                  "This connection is in use. Remove it after the run finishes.",
                )
              : code.endsWith("REVISION_CONFLICT")
                ? copy(
                    "连接已变化，请刷新后继续。",
                    "Connections changed. Refresh to continue.",
                  )
                : copy(
                    "操作未完成，请刷新连接状态后重试。",
                    "Action not completed. Refresh connections and retry.",
                  ),
        );
      }
    } finally {
      pending.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function testConnection() {
    if (busy) return;
    setConnectionTest(null);
    if (!form.model.trim()) {
      setMessage(copy("请先填写模型 ID。", "Enter a model ID first."));
      return;
    }
    const secret = keyInput.current?.value ?? "";
    const saved =
      selected?.settings.provider === form.provider &&
      selected.key_state === "configured"
        ? selected
        : null;
    if (!secret && !saved) {
      setMessage(
        copy(
          "请先填写 API Key。测试不需要填写价格或保存连接。",
          "Enter an API key. Testing does not require pricing or saving.",
        ),
      );
      keyInput.current?.focus();
      return;
    }
    setTesting(true);
    try {
      await perform(
        (signal) =>
          modelConnectionsApi.test(
            {
              provider: form.provider,
              model: form.model.trim(),
              ...(secret
                ? { key: secret }
                : { configuration_id: saved!.configuration_id }),
              expected_revision: catalog.data?.revision ?? 0,
              timeout_ms: Math.max(
                1000,
                Math.min(
                  20000,
                  Number.isFinite(form.timeout_ms) ? form.timeout_ms : 15000,
                ),
              ),
            },
            signal,
          ),
        setConnectionTest,
        (error) => {
          const code = isApiError(error) ? error.errorCode : "";
          return code === "CONNECTION_TEST_KEY_INVALID"
            ? copy(
                "API Key 格式无效，请重新复制，去掉空格和换行。",
                "Invalid API key format. Remove spaces and line breaks.",
              )
            : code.endsWith("REVISION_CONFLICT")
              ? copy(
                  "已保存的连接发生变化，请刷新后重试。",
                  "The saved connection changed. Refresh and retry.",
                )
              : code.endsWith("VAULT_UNAVAILABLE") ||
                  code.endsWith("KEY_MISSING")
                ? copy(
                    "无法读取已保存的密钥，请重新填入 API Key 后测试。",
                    "Cannot read the saved key. Enter it again to test.",
                  )
                : copy(
                    "测试未完成，请检查本地服务后重试。",
                    "Could not complete the check. Check the local service and retry.",
                  );
        },
      );
    } finally {
      if (alive.current) setTesting(false);
    }
  }
  function testMessage(result: ConnectionTestResult) {
    const messages: Record<string, [string, string]> = {
      verified: [
        "连接成功：密钥认证通过，模型已找到。",
        "Connected: API key authenticated and model found.",
      ],
      catalog_only: [
        "服务可达，模型已找到。模型列表不能独立证明密钥有效；尚未验证生成权限。",
        "Service reachable and model found. The catalog does not independently validate the key or generation access.",
      ],
      endpoint_only: [
        "服务接口已响应。该服务暂不支持无生成的密钥／模型检查，尚未验证密钥与生成权限。",
        "The service responded. This provider has no supported non-generating key/model check; key and generation access remain unverified.",
      ],
      model_not_listed: [
        "服务可达，但模型列表中没有此模型 ID，请核对模型名称。",
        "Service reachable, but the model ID was not listed. Check the model name.",
      ],
      balance_empty: [
        "密钥认证通过，但服务商报告余额不足。",
        "The key authenticated, but the provider reported insufficient balance.",
      ],
      authentication_failed: [
        "密钥无效或没有访问权限，请核对 API Key 与服务商。",
        "Invalid key or access denied. Check the API key and provider.",
      ],
      balance_or_plan: [
        "服务商拒绝访问，请检查余额或套餐权限。",
        "The provider refused access. Check balance or plan permissions.",
      ],
      rate_limited: [
        "服务商请求限流，请稍后重试。",
        "Provider rate limit reached. Retry later.",
      ],
      timeout: [
        "连接超时，请检查网络后重试。",
        "Connection timed out. Check your network and retry.",
      ],
      network_error: [
        "无法连接服务商，请检查网络、DNS 或证书。",
        "Cannot connect to the provider. Check network, DNS or certificates.",
      ],
      redirect_refused: [
        "服务商返回了重定向，未继续发送密钥。",
        "The provider redirected the request; the key was not forwarded.",
      ],
      provider_unavailable: [
        "服务商暂时不可用，请稍后重试。",
        "The provider is temporarily unavailable. Retry later.",
      ],
      inspection_unsupported: [
        "服务可达，但当前检查接口不受支持，密钥与模型尚未验证。",
        "Service reachable, but this check endpoint is unsupported; key and model remain unverified.",
      ],
      unexpected_response: [
        "服务商响应格式不符合预期，未判定连接成功。",
        "Unexpected provider response; success could not be confirmed.",
      ],
      response_too_large: [
        "服务商响应过大，检查已停止。",
        "Provider response exceeded the check limit.",
      ],
      provider_rejected: [
        "服务商拒绝了检查请求，请核对连接配置。",
        "The provider rejected the check. Review the connection settings.",
      ],
      connection_failed: [
        "连接检查未完成，请稍后重试。",
        "Connection check failed. Retry later.",
      ],
    };
    const text = messages[result.code] ?? messages.connection_failed;
    return `${copy(...text)} ${copy("耗时", "Elapsed")} ${result.latency_ms} ms。`;
  }
  function save() {
    if (
      manualOutput &&
      (!Number.isSafeInteger(form.max_output_tokens) ||
        (form.max_output_tokens ?? 0) < 1)
    ) {
      setMessage(
        copy(
          "请在高级设置中填写正整数输出上限，或切换为自动。",
          "Enter a positive output limit in advanced settings, or choose automatic.",
        ),
      );
      return;
    }
    if (!form.model.trim()) {
      setMessage(copy("请填写模型 ID。", "Enter a model ID."));
      return;
    }
    if (
      form.pricing_mode === "estimate" &&
      ![form.input_per_million, form.output_per_million].every(
        (value) =>
          value !== null && /^(\d+)(\.\d+)?$/.test(value) && Number(value) > 0,
      )
    ) {
      setAdvanced(true);
      setMessage(
        copy(
          "请填写两项估算单价，或关闭费用估算后直接保存。",
          "Enter both estimated rates, or turn cost estimation off to save.",
        ),
      );
      return;
    }
    const secret = keyInput.current?.value ?? "";
    if (
      !secret &&
      (!selected || selected.settings.provider !== form.provider)
    ) {
      setMessage(
        copy("请填写此服务的 API Key。", "Enter the API key for this service."),
      );
      keyInput.current?.focus();
      return;
    }
    if (keyInput.current) keyInput.current.value = "";
    void perform(
      (signal) =>
        modelConnectionsApi.save(
          {
            expected_revision: revision.current,
            settings: {
              ...form,
              model: form.model.trim(),
              output_length_mode: manualOutput ? "manual" : "auto",
              max_output_tokens: manualOutput ? form.max_output_tokens : null,
            },
            ...(secret ? { key: secret } : {}),
            ...(selected
              ? { source_configuration_id: selected.configuration_id }
              : {}),
            new_connection: !selected,
            display_name: form.model.trim(),
          },
          signal,
        ),
      async (result) => {
        client.setQueryData(["research", "provider"], result);
        const data = await refresh();
        const item = data?.connections.find(
          (connection) =>
            connection.configuration_id === result.configuration_id,
        );
        if (!alive.current) return;
        setSelection(item?.id ?? null);
        if (item)
          setForm({
            ...item.settings,
            price_ceiling_confirmed: false,
            billing_limit_acknowledged: false,
          });
        revision.current = data?.revision ?? 0;
        setDirty(false);
        setMessage(copy("已保存", "Saved"));
      },
    );
  }
  async function activate() {
    if (!selected) return;
    await perform(
      (signal) =>
        modelConnectionsApi.activate(
          selected.configuration_id,
          catalog.data!.revision,
          signal,
        ),
      async (result) => {
        client.setQueryData(["research", "provider"], result);
        const data = await refresh();
        revision.current = data?.revision ?? 0;
        setMessage(copy("已设为当前模型", "Current model updated"));
      },
    );
  }
  async function remove() {
    if (!selected) return;
    await perform(
      (signal) =>
        modelConnectionsApi.remove(
          selected.configuration_id,
          catalog.data!.revision,
          signal,
        ),
      async () => {
        await refresh();
        setSelection(null);
        setRemoveOpen(false);
        setDirty(false);
        setMessage("");
      },
    );
  }
  function discard() {
    const target = pendingRoute;
    if (keyInput.current) keyInput.current.value = "";
    setPendingRoute(null);
    setDirty(false);
    if (target) navigateToAppRoute(router, target);
    else choose(pendingSelection ?? "");
  }
  return (
    <section
      className="model-connections"
      aria-label={copy("AI 模型连接", "AI model connections")}
    >
      <header className="settings-section-heading">
        <h2>{copy("AI 模型连接", "AI model connections")}</h2>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => switchTo("")}
        >
          <PlusIcon className="size-4" />
          {copy("新增连接", "Add connection")}
        </Button>
      </header>
      {catalog.isPending && (
        <p role="status">{copy("读取连接…", "Loading connections…")}</p>
      )}
      {catalog.isError && (
        <div role="alert">
          <p>
            {copy("未能读取模型连接。", "Could not load model connections.")}
          </p>
          <Button variant="outline" onClick={() => void catalog.refetch()}>
            {copy("重试", "Retry")}
          </Button>
        </div>
      )}
      {catalog.data && (
        <div className="model-connection-layout">
          <nav
            className="model-connection-list"
            aria-label={copy("我的连接", "My connections")}
          >
            <h3>{copy("我的连接", "My connections")}</h3>
            {items.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-current={selection === item.id ? "true" : undefined}
                onClick={() => switchTo(item.id)}
                disabled={busy}
              >
                <PlugIcon className="size-4" />
                <span>
                  <strong>{item.name}</strong>
                  <small>
                    {item.endpoint
                      ? new URL(item.endpoint).hostname
                      : item.settings.provider}
                  </small>
                  {item.is_current && (
                    <em>
                      {item.enabled
                        ? copy("当前使用", "Current")
                        : copy("已暂停", "Paused")}
                    </em>
                  )}
                  {item.key_state === "delete_pending" && (
                    <em>{copy("移除未完成", "Removal pending")}</em>
                  )}
                </span>
              </button>
            ))}
            {!items.length && (
              <p className="settings-muted">
                {copy("从一个模型开始。", "Start with a model.")}
              </p>
            )}
            {moreOffset !== null && (
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() =>
                  void perform(
                    (signal) => modelConnectionsApi.list(signal, moreOffset),
                    (page) => {
                      setExtra([...extra, ...page.connections]);
                      setNextOffset(
                        page.next_offset === null ? -1 : page.next_offset,
                      );
                    },
                  )
                }
              >
                {copy("更多连接", "More connections")}
              </Button>
            )}
          </nav>
          <div className="model-connection-editor">
            <div className="model-editor-heading">
              <h3>
                {selected
                  ? copy("模型连接", "Model connection")
                  : copy("新增模型连接", "New model connection")}
              </h3>
              {inspected === selected?.configuration_id && (
                <span className="model-local-status">
                  <CheckIcon className="size-3.5" />
                  {copy("配置完整", "Configuration complete")}
                </span>
              )}
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                save();
              }}
            >
              <fieldset
                disabled={busy || selected?.key_state === "delete_pending"}
              >
                <label>
                  {copy("连接服务", "Service")}
                  <SearchSelect
                    value={form.provider}
                    onChange={(event) =>
                      change({
                        provider: event.target.value,
                        price_ceiling_confirmed: false,
                        billing_limit_acknowledged: false,
                      })
                    }
                  >
                    {catalog.data.profiles.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </SearchSelect>
                </label>
                <label>
                  {copy("模型名称 / 模型 ID", "Model name / model ID")}
                  <input
                    value={form.model}
                    autoComplete="off"
                    maxLength={200}
                    onChange={(event) =>
                      change({
                        model: event.target.value,
                        price_ceiling_confirmed: false,
                      })
                    }
                  />
                </label>
                <label>
                  {copy("API 地址", "API endpoint")}
                  <input
                    value={profile?.endpoint ?? selected?.endpoint ?? ""}
                    readOnly
                    spellCheck={false}
                  />
                </label>
                <label>
                  API Key
                  <input
                    ref={keyInput}
                    type="password"
                    autoComplete="new-password"
                    maxLength={4096}
                    placeholder={
                      selected && selected.settings.provider === form.provider
                        ? copy(
                            "已保存；留空可继续使用",
                            "Saved; leave blank to keep",
                          )
                        : copy("填入 API Key", "Enter API key")
                    }
                    onChange={() => {
                      setDirty(true);
                      setInspected("");
                      setConnectionTest(null);
                      setMessage("");
                    }}
                  />
                </label>
                <details
                  className="model-usage"
                  open={advanced}
                  onToggle={(event) => setAdvanced(event.currentTarget.open)}
                >
                  <summary>
                    {copy(
                      "高级设置 · 费用估算（可选）",
                      "Advanced · Optional cost estimates",
                    )}
                  </summary>
                  <label className="model-confirm">
                    <input
                      type="checkbox"
                      checked={form.pricing_mode === "estimate"}
                      onChange={(event) =>
                        change({
                          pricing_mode: event.target.checked
                            ? "estimate"
                            : "off",
                          input_per_million: null,
                          output_per_million: null,
                        })
                      }
                    />
                    {copy("启用费用估算", "Enable cost estimates")}
                  </label>
                  {form.pricing_mode === "estimate" && (
                    <div className="model-usage-grid">
                      <label>
                        {copy(
                          "输入 / 百万 tokens（USD）",
                          "Input / million tokens (USD)",
                        )}
                        <input
                          inputMode="decimal"
                          value={form.input_per_million ?? ""}
                          onChange={(event) =>
                            change({ input_per_million: event.target.value })
                          }
                        />
                      </label>
                      <label>
                        {copy(
                          "输出 / 百万 tokens（USD）",
                          "Output / million tokens (USD)",
                        )}
                        <input
                          inputMode="decimal"
                          value={form.output_per_million ?? ""}
                          onChange={(event) =>
                            change({ output_per_million: event.target.value })
                          }
                        />
                      </label>
                    </div>
                  )}
                  <p>
                    {copy(
                      "不启用也能保存和使用。软件仍记录 token 用量，费用显示为未估算；实际账单以服务商为准。",
                      "You can save and use the connection without estimates. Token usage is still recorded, costs remain unestimated, and provider billing applies.",
                    )}
                  </p>
                </details>
                <details className="model-usage">
                  <summary>
                    {copy(
                      "高级设置 · 输出长度：",
                      "Advanced · Output length: ",
                    )}
                    {manualOutput
                      ? copy("手动", "Manual")
                      : copy(
                          "自动（服务商默认）",
                          "Automatic (provider default)",
                        )}
                  </summary>
                  <label className="model-confirm">
                    <input
                      type="checkbox"
                      checked={manualOutput}
                      onChange={(event) => {
                        setManualOutput(event.target.checked);
                        change({
                          output_length_mode: event.target.checked
                            ? "manual"
                            : "auto",
                          max_output_tokens: null,
                        });
                      }}
                    />
                    {copy("手动限制输出长度", "Set a manual output limit")}
                  </label>
                  {manualOutput && (
                    <label>
                      {copy("最大输出 tokens", "Maximum output tokens")}
                      <input
                        type="number"
                        min={1}
                        step={1}
                        placeholder={copy(
                          "填写正整数",
                          "Enter a positive integer",
                        )}
                        value={form.max_output_tokens ?? ""}
                        onChange={(event) =>
                          change({
                            max_output_tokens:
                              event.target.value === ""
                                ? null
                                : Number(event.target.value),
                          })
                        }
                      />
                    </label>
                  )}
                  <p>
                    {copy(
                      "自动模式不发送输出长度参数，由服务商决定。",
                      "Automatic mode omits output-length parameters and uses the provider default.",
                    )}
                  </p>
                </details>
              </fieldset>
              {message && (
                <p className="model-message" role="status">
                  {message}
                </p>
              )}
              {connectionTest && (
                <p className="model-message" role="status">
                  {testMessage(connectionTest)}
                </p>
              )}
              <div className="model-form-actions">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || selected?.key_state === "delete_pending"}
                  onClick={() => void testConnection()}
                >
                  {testing
                    ? copy("正在测试…", "Testing…")
                    : copy("测试连接", "Test connection")}
                </Button>
                <Button
                  type="submit"
                  disabled={
                    busy ||
                    selected?.key_state === "delete_pending" ||
                    (!dirty && !!selected)
                  }
                >
                  {busy && !testing
                    ? copy("处理中…", "Working…")
                    : copy("保存并设为当前模型", "Save as current model")}
                </Button>
                {selected &&
                  !dirty &&
                  (!selected.is_current || !selected.enabled) && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy || selected.key_state !== "configured"}
                      onClick={() => void activate()}
                    >
                      {copy("设为当前模型", "Use this model")}
                    </Button>
                  )}
              </div>
              <p className="settings-muted">
                {copy(
                  "测试连接不需要价格确认，也不会保存配置或发送项目数据。只检查服务和可用的认证／模型接口，不执行文本生成。",
                  "Connection testing needs no pricing confirmation and does not save settings or send project data. It checks service and available authentication/model endpoints without generating text.",
                )}
              </p>
            </form>
            {selected && (
              <div className="model-secondary-actions">
                <button
                  type="button"
                  disabled={
                    busy || dirty || selected.key_state !== "configured"
                  }
                  onClick={() =>
                    void perform(
                      (signal) =>
                        modelConnectionsApi.inspect(
                          selected.configuration_id,
                          catalog.data!.revision,
                          signal,
                        ),
                      (result) => {
                        if (result.configuration_valid)
                          setInspected(selected.configuration_id);
                      },
                    )
                  }
                >
                  {copy("检查配置", "Check configuration")}
                </button>
                {selected.is_current && selected.enabled && (
                  <button
                    type="button"
                    disabled={busy || dirty}
                    onClick={() =>
                      void perform(
                        (signal) =>
                          modelConnectionsApi.enable(
                            catalog.data!.revision,
                            false,
                            signal,
                          ),
                        async (result) => {
                          client.setQueryData(["research", "provider"], result);
                          const data = await refresh();
                          revision.current = data?.revision ?? 0;
                          setMessage(copy("已暂停使用", "Model paused"));
                        },
                      )
                    }
                  >
                    {copy("暂停使用", "Pause model")}
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRemoveOpen(true)}
                >
                  {copy("移除此连接", "Remove connection")}
                </button>
              </div>
            )}
            <button
              className="model-refresh"
              type="button"
              disabled={busy}
              onClick={() =>
                void perform(
                  () => refresh(),
                  () => undefined,
                )
              }
            >
              <RefreshCwIcon className="size-3.5" />
              {copy("刷新状态", "Refresh status")}
            </button>
          </div>
        </div>
      )}
      <details className="model-maintenance">
        <summary>{copy("连接维护", "Connection maintenance")}</summary>
        <ProviderMaintenance
          revision={catalog.data?.revision ?? 0}
          changed={refresh}
        />
      </details>
      <Dialog
        open={pendingSelection !== null || pendingRoute !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingSelection(null);
            setPendingRoute(null);
          }
        }}
      >
        <DialogContent>
          <DialogTitle>
            {copy("保留模型连接的编辑？", "Keep connection edits?")}
          </DialogTitle>
          <DialogDescription>
            {copy(
              "当前连接有未保存的更改。",
              "This connection has unsaved changes.",
            )}
          </DialogDescription>
          <div className="model-form-actions">
            <Button
              variant="outline"
              onClick={() => {
                setPendingSelection(null);
                setPendingRoute(null);
              }}
            >
              {copy("继续编辑", "Keep editing")}
            </Button>
            <Button onClick={discard}>
              {copy("放弃更改", "Discard changes")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={removeOpen}
        onOpenChange={(open) => {
          if (!busy) setRemoveOpen(open);
        }}
      >
        <DialogContent>
          <DialogTitle>
            {copy("移除此连接？", "Remove connection?")}
          </DialogTitle>
          <DialogDescription>
            {copy(
              "此连接和本机保存的 API Key 将被移除。",
              "This connection and its locally saved API key will be removed.",
            )}
          </DialogDescription>
          <div className="model-form-actions">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setRemoveOpen(false)}
            >
              {copy("取消", "Cancel")}
            </Button>
            <Button disabled={busy} onClick={() => void remove()}>
              {copy("移除", "Remove")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
