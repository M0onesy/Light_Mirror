/** Financial SDK consumer for the unmodified DeepSeek Harness AgentLoop. */
import { createHash, randomBytes } from 'node:crypto';
import { AsyncLocalStorage } from 'node:async_hooks';
import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { Service } from '@deepseek-ai/cordis';
import { brandString } from '@deepseek-ai/dsh-brand';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { JsonRpcLineTransport } from '@deepseek-ai/dsh-sdk-protocol';
import { CredentialProvider } from '@deepseek-ai/dsh-credentials';
import { launchEnvironmentOf } from '@deepseek-ai/dsh-launch-environment';
import { assertSupportedJsonSchema, validateJsonSchemaValue } from '@deepseek-ai/dsh-tools';
import * as DeepSeek from '@deepseek-ai/dsh-llm-deepseek';
import * as PiAi from '@deepseek-ai/dsh-llm-pi-ai';

export const name = 'mingjing-finance-rpc';
export const inject = ['agents', 'llm', 'tools', 'systemPrompt', 'sessionPersistence'];
const VERSION = '0.4.0';
const PROTOCOL_VERSION = 1;
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const object = (value, label) => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${label} must be an object`);
  return value;
};
const text = (value, label) => {
  if (typeof value !== 'string' || value.length === 0 || value.length > 4096) throw new TypeError(`${label} must be a non-empty string`);
  return value;
};
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const ROLES = new Set(['planner', 'verifier', 'reporter', 'question', 'coordinator', 'probe', 'binding-a', 'binding-b', 'binding_a', 'binding_b']);
const SCHEMA_KEYS = new Set(['type','properties','required','additionalProperties','items','enum','const','oneOf','description','title','default','examples']);
function harnessSchema(schema) {
  const result = {};
  for (const [key, value] of Object.entries(object(schema, 'tool schema'))) {
    if (!SCHEMA_KEYS.has(key)) continue;
    if (key === 'properties') result[key] = Object.fromEntries(Object.entries(value).map(([name, child]) => [name, harnessSchema(child)]));
    else if (key === 'items') result[key] = harnessSchema(value);
    else if (key === 'oneOf') result[key] = value.map(harnessSchema);
    else result[key] = structuredClone(value);
  }
  return result;
}

/** Adapter-local HTTP bridge: real API paths, verified TLS, bounded redirects.
 * Upstream adapters expose no fetch hook. A private loopback route therefore
 * preserves their serializers/streams without replacing process-wide fetch.
 */
class FinancialRouteProxy {
  constructor() { this.routes = new Map(); this.server = undefined; this.starting = undefined; }
  async start() {
    if (this.starting) return this.starting;
    this.starting = new Promise((resolveStart, reject) => {
      this.server = createServer((request, response) => { void this.forward(request, response); });
      this.server.once('error', reject);
      this.server.listen(0, '127.0.0.1', () => {
        this.server.off('error', reject);
        this.origin = `http://127.0.0.1:${this.server.address().port}`;
        resolveStart();
      });
    });
    return this.starting;
  }
  async mount(route) {
    await this.start();
    const upstream = new URL(route.base_url);
    if (!['https:','http:'].includes(upstream.protocol) || upstream.username || upstream.password || upstream.search || upstream.hash) throw new TypeError('Invalid financial API origin');
    if (upstream.protocol === 'http:' && !['127.0.0.1','localhost','[::1]'].includes(upstream.hostname)) throw new TypeError('Financial API requires HTTPS outside loopback');
    const chatPath = route.chat_path ?? '/chat/completions';
    if (typeof chatPath !== 'string' || !chatPath.startsWith('/') || chatPath.startsWith('//') || /[?#\\\x00-\x20]/.test(chatPath)) throw new TypeError('Invalid financial chat API path');
    const token = randomBytes(24).toString('hex');
    this.routes.set(token, { upstream, route });
    return `${this.origin}/${token}`;
  }
  async forward(request, response) {
    const abort = new AbortController();
    request.on('aborted', () => abort.abort());
    response.on('close', () => { if (!response.writableEnded) abort.abort(); });
    try {
      const local = new URL(request.url, this.origin);
      const [, token, ...segments] = local.pathname.split('/');
      const registered = this.routes.get(token);
      const suffix = '/'+segments.join('/');
      if (!registered || request.method !== 'POST' || !['/chat/completions','/responses','/messages','/v1/messages'].includes(suffix)) throw new Error('UNAUTHORIZED_FINANCIAL_ENDPOINT');
      const { upstream, route } = registered;
      const path = (suffix === '/chat/completions' && ['deepseek','openai-completions'].includes(route.protocol ?? 'openai-completions'))
        ? (route.chat_path ?? '/chat/completions') : suffix;
      let target = new URL(upstream.toString().replace(/\/+$/, '')+path+local.search);
      if (target.origin !== upstream.origin) throw new Error('CROSS_ORIGIN_ENDPOINT_REJECTED');
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 16*1024*1024) throw new Error('FINANCIAL_REQUEST_TOO_LARGE');
        chunks.push(chunk);
      }
      let body = Buffer.concat(chunks), method = 'POST';
      const headers = new Headers();
      for (const key of ['authorization','x-api-key','anthropic-version','anthropic-beta','content-type','accept',
                         'x-deepseek-harness-session-id','x-deepseek-harness-compact','x-deepseek-user-id']) {
        if (typeof request.headers[key] === 'string') headers.set(key, request.headers[key]);
      }
      let fetched;
      for (let redirects = 0; redirects <= 3; redirects++) {
        fetched = await fetch(target, { method, body, headers, redirect: 'manual', signal: abort.signal });
        if (![301,302,303,307,308].includes(fetched.status)) break;
        const location = fetched.headers.get('location');
        await fetched.body?.cancel();
        if (!location || redirects === 3) throw new Error('API_REDIRECT_LIMIT');
        const next = new URL(location, target);
        if (next.origin !== upstream.origin || (target.protocol === 'https:' && next.protocol !== 'https:') || next.username || next.password) throw new Error('API_CROSS_ORIGIN_REDIRECT_REJECTED');
        if (fetched.status === 303 || ([301,302].includes(fetched.status) && method === 'POST')) { method = 'GET'; body = undefined; }
        target = next;
      }
      if (!fetched.ok) {
        await fetched.body?.cancel();
        response.writeHead(fetched.status, { 'Content-Type': 'application/json',
          ...(fetched.headers.get('retry-after') ? { 'Retry-After': fetched.headers.get('retry-after') } : {}) });
        response.end(JSON.stringify({ error: { message: `FINANCIAL_PROVIDER_HTTP_${fetched.status}`, type: 'financial_transport_error' } }));
        return;
      }
      response.writeHead(fetched.status, { 'Content-Type': fetched.headers.get('content-type') ?? 'application/json',
        ...(fetched.headers.get('retry-after') ? { 'Retry-After': fetched.headers.get('retry-after') } : {}) });
      if (!fetched.body) { response.end(); return; }
      const readable = Readable.fromWeb(fetched.body);
      readable.on('error', () => response.destroy());
      readable.pipe(response);
    } catch (error) {
      if (response.headersSent || response.destroyed) { response.destroy(); return; }
      const code = /^(API_|UNAUTHORIZED_|CROSS_ORIGIN_|FINANCIAL_)[A-Z_]+$/.test(error.message) ? error.message : 'FINANCIAL_PROVIDER_TRANSPORT_FAILED';
      response.writeHead(502, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: code, type: 'financial_transport_error' } }));
    }
  }
  async close() {
    this.routes.clear();
    if (!this.server) return;
    await new Promise(resolveClose => { this.server.close(resolveClose); this.server.closeAllConnections(); });
  }
}

/** Resolve only references registered by the trusted host; never persist secrets. */
class BridgeCredentials extends CredentialProvider {
  constructor(ctx, transport, references, context) { super(ctx); this.transport = transport; this.references = references; this.context = context; }
  async resolve(ref) {
    const owned = this.references.get(ref);
    if (!owned) return undefined;
    const current = this.context.getStore();
    if (!current || current.route_hash !== owned.route_hash) throw new Error('Credential request has no active financial model owner');
    current.signal?.throwIfAborted();
    const result = object(await this.transport.request('mj/credentials.resolve', {
      credential_ref: owned.credential_ref, provider_id: owned.provider_id,
      run_id: current.run_id, session_id: current.session_id, role: current.role, resource_lock: current.resource_lock,
    }, current.signal), 'credential response');
    current.signal?.throwIfAborted();
    return { value: text(result.api_key, 'api_key'), source: 'mingjing-dpapi-rpc' };
  }
  async describe(ref) { return { configured: this.references.has(ref), source: 'mingjing-dpapi-rpc', writable: false }; }
  async set() { throw new Error('Credentials are managed by MingJing DPAPI'); }
  async unset() { throw new Error('Credentials are managed by MingJing DPAPI'); }
  async readRecord() { return undefined; }
  async describeRecord() { return { configured: false, writable: false }; }
  async listRecords() { return []; }
  async modifyRecord() { throw new Error('OAuth records are not supported by the DPAPI reference bridge'); }
  async deleteRecord() { throw new Error('Credentials are managed by MingJing DPAPI'); }
}

/** Session-owned model/tool configuration and durable scope restoration. */
class FinancialRuntime extends Service {
  constructor(ctx, transport) {
    super(ctx, 'mingjingRuntime');
    this.transport = transport;
    this.records = new Map();
    this.pending = new Map();
    this.references = new Map();
    this.credentialContext = new AsyncLocalStorage();
    this.routeProxy = new FinancialRouteProxy();
    this.routes = new Map();
    this.adapters = [];
    this.hostTools = new Map();
    this.closing = false;
    this.modelCalls = 0;
    this.toolCalls = 0;
    this.tokens = 0;
    this.startedAt = Date.now();
    this.home = resolve(text(process.env.DSH_HOME, 'DSH_HOME'));
    this.disposers = [];
    new BridgeCredentials(ctx, transport, this.references, this.credentialContext);
    this.disposers.push(ctx.on('session/event', (session, event) => transport.notify('session.event', { sessionId: String(session.id), event })));
    this.disposers.push(ctx.on('agent/status', ({ agent, status }) => transport.notify('session.status', { sessionId: String(agent.session.id), status })));
    this.disposers.push(ctx.on('session/created', session => {
      if (session.header.parentSession) transport.notify('subagent.started', { parentSessionId: String(session.header.parentSession), childSessionId: String(session.id) });
    }));
    const runtime = this;
    this.disposers.push(ctx.on('llm/stream', async function* (options, next) {
      const record = runtime.records.get(String(options.sessionId));
      if (!record) throw new Error('Model request has no financial session owner');
      runtime.checkBudget(record.spec, 'model');
      const request = {
        provider: record.spec.route.provider, protocol: record.spec.route.protocol,
        base_url: record.spec.route.base_url, model: options.model,
        messages: options.messages.map(message => ({ role: message.role, content: message.content,
          ...(message.source?.kind === 'model' && message.source.replayState !== undefined ? { replay_state: message.source.replayState } : {}) })),
        ...(options.system === undefined ? {} : { system: options.system }),
        tools: options.tools ?? [], max_tokens: options.maxTokens,
        temperature: options.temperature ?? record.spec.route.temperature ?? undefined,
        reasoning_effort: options.reasoningEffort,
      };
      const request_hash = hash(request);
      const envelope = { run_id: record.spec.run_id, session_id: record.spec.session_id, role: record.spec.role,
        resource_lock: record.spec.resource_lock, request_hash, request };
      if (record.spec.model_cache !== false) {
        const cached = object(await runtime.transport.request('mj/model.cache.get', envelope, options.signal), 'model cache');
        if (cached.hit) {
          if (!Array.isArray(cached.chunks)) throw new Error('Invalid model replay chunks');
          runtime.recordTokens(cached.chunks);
          for (const chunk of cached.chunks) { options.signal?.throwIfAborted(); yield chunk; }
          return;
        }
      }
      if (record.spec.replay_only) throw new Error('REPLAY_CACHE_MISS: network access is prohibited');
      const chunks = [];
      // Routes/adapters may be shared by independent A/B sessions and later
      // batches. Bind credentials to the currently advancing model stream,
      // never the session that first happened to mount the adapter.
      const credentials = { run_id: record.spec.run_id, session_id: record.spec.session_id,
        role: record.spec.role, resource_lock: record.spec.resource_lock,
        route_hash: hash(record.spec.route), signal: options.signal };
      const iterator = runtime.credentialContext.run(credentials, () => next()[Symbol.asyncIterator]());
      try {
        while (true) {
          const item = await runtime.credentialContext.run(credentials, () => iterator.next());
          if (item.done) break;
          chunks.push(JSON.parse(JSON.stringify(item.value)));
          yield item.value;
        }
      } finally {
        if (iterator.return) await runtime.credentialContext.run(credentials, () => iterator.return());
      }
      const finish = chunks.findLast(chunk => chunk.type === 'finish');
      runtime.recordTokens(chunks);
      if (record.spec.model_cache !== false && finish && !['error','aborted'].includes(finish.reason?.kind ?? finish.reason)) {
        await runtime.transport.request('mj/model.cache.put', { ...envelope, chunks }, options.signal);
      }
    }));
  }

  describe() {
    return { protocol_version: PROTOCOL_VERSION, plugin_version: VERSION, harness_version: '0.1.6-alpha.2',
      node_version: process.version, platform: process.platform, architecture: process.arch,
      engine: '@deepseek-ai/dsh-agent-loop', native_loop: true, profile: 'mingjing',
      credential_store: 'mingjing-dpapi-rpc', shell: false, code_mode: false, telemetry: false,
      session_storage: { configured: Boolean(this.ctx.sessionPersistence), root: this.ctx.sessionPersistence?.root },
      provider_transport: { local_route_proxy: true, custom_chat_path: true, tls_validation: true, cross_origin_redirects: false },
      budget_usage: { model_calls: this.modelCalls, tool_calls: this.toolCalls, total_tokens: this.tokens, elapsed_s: (Date.now() - this.startedAt) / 1000 },
      methods: ['mj/runtime.describe','mj/session.open','mj/session.prompt','mj/session.cancel','mj/session.resume','mj/session.close'],
      sessions: [...this.records.values()].map(r => ({ session_id: r.spec.session_id, role: r.spec.role, run_id: r.spec.run_id, status: r.handle.agent.status })) };
  }

  checkBudget(spec, operation) {
    const budget = spec.budget ?? {};
    if (Date.now() - this.startedAt >= (budget.max_runtime_s ?? 1800) * 1000) throw new Error('MingJing runtime budget exhausted');
    if (this.tokens >= (budget.max_total_tokens ?? 250000)) throw new Error('MingJing token budget exhausted');
    if (operation === 'model' && ++this.modelCalls > (budget.max_model_calls ?? 128)) throw new Error('MingJing model-call budget exhausted');
    if (operation === 'tool' && ++this.toolCalls > (budget.max_tool_calls ?? 1000)) throw new Error('MingJing tool-call budget exhausted');
  }

  recordTokens(chunks) {
    for (const chunk of chunks) if (chunk.type === 'usage') {
      const usage = chunk.usage;
      this.tokens += usage.totalTokens ?? ((usage.inputTokens ?? 0) + (usage.outputTokens ?? 0) + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0));
    }
  }

  async awaitHostTools(sessionId) {
    const pending = this.hostTools.get(sessionId);
    if (pending) await Promise.allSettled([...pending]);
  }

  validateSpec(raw) {
    const spec = structuredClone(object(raw, 'session request'));
    if (!SESSION_ID.test(text(spec.session_id, 'session_id'))) throw new TypeError('Unsafe session_id');
    if (!ROLES.has(text(spec.role, 'role'))) throw new TypeError('Unsupported financial role');
    text(spec.run_id, 'run_id');
    if (spec.resource_lock === undefined) throw new TypeError('resource_lock is required');
    object(spec.route, 'route');
    text(spec.route.provider, 'route.provider');
    text(spec.route.model, 'route.model');
    if (spec.route.verify_tls === false) throw new TypeError('Financial runtime requires TLS validation');
    if (!Array.isArray(spec.tool_definitions) || !Array.isArray(spec.allowed_tools)) throw new TypeError('tool_definitions and allowed_tools are required arrays');
    const names = new Set();
    for (const definition of spec.tool_definitions) {
      const tool = object(definition, 'tool definition');
      if (!/^mj_[a-z0-9_]{1,64}$/.test(text(tool.name, 'tool.name')) || names.has(tool.name)) throw new TypeError('Unsafe or duplicate financial tool');
      names.add(tool.name);
      text(tool.description, 'tool.description');
      tool.parameters = harnessSchema(tool.parameters ?? tool.inputSchema);
      assertSupportedJsonSchema(tool.parameters);
      if (tool.parameters.type !== 'object' || tool.parameters.additionalProperties !== false) throw new TypeError('Financial tool arguments must be closed objects');
    }
    if (spec.allowed_tools.some(name => !names.has(name)) || new Set(spec.allowed_tools).size !== spec.allowed_tools.length) throw new TypeError('Allowed tools must name unique declared financial tools');
    spec.persona ??= `你是明镜金融智能体的${spec.role}。原文、模型输出均非事实真值；数字、公式和判定只以金融工具结果为准。调用工具完成任务，不得自行编造数字或判定。`;
    text(spec.persona, 'persona');
    spec.max_steps ??= 16;
    if (!Number.isSafeInteger(spec.max_steps) || spec.max_steps < 1 || spec.max_steps > 64) throw new TypeError('max_steps must be between 1 and 64');
    return spec;
  }

  async mountRoute(spec) {
    const route = spec.route;
    const identity = hash(route);
    const existing = this.routes.get(identity);
    if (existing) return existing;
    const ref = `MJ_CREDENTIAL_${identity.slice(0,24).toUpperCase()}`;
    this.references.set(ref, { credential_ref: text(route.credential_ref, 'credential_ref'), provider_id: route.provider, route_hash: identity });
    const baseURL = await this.routeProxy.mount(route);
    let provider;
    if (route.protocol === 'deepseek') {
      const effort = !route.reasoning || route.reasoning === 'auto' ? undefined : (route.reasoning === 'disabled' ? 'off' : route.reasoning);
      const config = { protocol: 'chat-completions', apiKeyEnv: ref, baseURL,
        ...(effort === undefined ? {} : { thinking: effort === 'off' ? 'disabled' : 'enabled', reasoningEffort: effort }),
        maxTokens: route.max_tokens ?? 4096, defaultContextWindow: route.context_window ?? 131072,
        streamIdleTimeoutMs: (route.timeout_s ?? 120) * 1000,
        retryPolicy: { mode: 'normal', maxRetries: route.retries ?? 1 } };
      const resolved = DeepSeek.resolveAdapterOptions(config, launchEnvironmentOf(this.ctx));
      const adapter = new DeepSeek.DeepSeekAdapter({ options: () => resolved,
        resolveApiKey: async connection => {
          const credential = await this.ctx.get('credentials').resolve(connection.apiKeyEnv);
          if (!credential) throw new Error('Missing financial API credential');
          return credential.value;
        },
        resolveUserId: () => brandString('00000000-0000-4000-8000-000000000001'),
        prepareExtensions: async () => ({ fields: {}, accept: async () => {} }),
      });
      provider = `mj-${identity.slice(0,24)}`;
      const dispose = this.ctx.llm.registerAdapter([provider], adapter);
      this.adapters.push({ dispose: async () => dispose() });
    } else {
      provider = `mj-${identity.slice(0,24)}`;
      const profile = { displayName: route.provider, apiKeyEnv: ref, api: route.protocol ?? 'openai-completions',
        baseURL, timeoutMs: (route.timeout_s ?? 120) * 1000,
        streamIdleTimeoutMs: (route.timeout_s ?? 120) * 1000,
        retryPolicy: { mode: 'normal', maxRetries: route.retries ?? 1 },
        models: [{ id: route.model, name: route.model, contextWindow: route.context_window ?? 131072, maxTokens: route.max_tokens ?? 4096 }] };
      if (route.compat && Object.keys(route.compat).length) profile.compat = route.compat;
      if (route.reasoning && !['off','disabled','auto'].includes(route.reasoning)) profile.reasoning = route.reasoning;
      this.adapters.push(await this.ctx.plugin(PiAi, { providers: { [provider]: profile } }));
    }
    this.routes.set(identity, provider);
    return provider;
  }

  async create(raw, resume = false) {
    if (this.closing) throw new Error('Runtime is shutting down');
    const spec = this.validateSpec(raw);
    if (this.records.has(spec.session_id) || this.pending.has(spec.session_id)) throw new Error('Session already active');
    const creation = this.createOwned(spec, resume);
    this.pending.set(spec.session_id, creation);
    try { return await creation; } finally { this.pending.delete(spec.session_id); }
  }

  async createOwned(spec, resume) {
    const descriptorDir = join(this.home, 'mingjing-session-descriptors');
    const descriptor = join(descriptorDir, `${spec.session_id}.json`);
    if (resume) {
      const stored = JSON.parse(await readFile(descriptor, 'utf8'));
      if (hash(stored) !== hash(spec)) throw new Error('Session configuration/resource lock differs from the persisted descriptor');
    }
    const provider = await this.mountRoute(spec);
    const agentOptions = { provider, model: spec.route.model, maxTokens: spec.route.max_tokens ?? 4096 };
    if (spec.route.protocol === 'deepseek' && spec.route.reasoning && spec.route.reasoning !== 'auto') agentOptions.reasoningEffort = spec.route.reasoning === 'disabled' ? 'off' : spec.route.reasoning;
    await this.ctx.llm.resolveCallConfig(agentOptions);
    let steps = 0;
    const setup = (agentCtx, agent) => {
      agentCtx.systemPrompt.section({ name: 'deployment:persona-prefix', order: 0, text: spec.persona, interpolate: false });
      const allowed = new Set(spec.allowed_tools);
      for (const definition of spec.tool_definitions) {
        if (!allowed.has(definition.name)) continue;
        agentCtx.tools.register({ ...definition, output: { schema: {}, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
          execute: async (params, exec) => {
            if (exec.agent !== agent || !allowed.has(exec.name)) throw new Error('Financial tool authority mismatch');
            this.checkBudget(spec, 'tool');
            const violations = validateJsonSchemaValue(definition.parameters, params, 'params');
            if (violations.length) throw new TypeError(`Invalid financial tool arguments: ${violations.join('; ')}`);
            const call = { session_id: spec.session_id, role: spec.role, call_id: String(exec.callId),
              run_id: spec.run_id, resource_lock: spec.resource_lock, tool_name: definition.name, params };
            // Cancellation must wait for the authoritative host operation to
            // settle; aborting only the RPC waiter would leave a live write
            // behind a claimed quiescent cancelled session.
            let pending = this.hostTools.get(spec.session_id);
            if (!pending) this.hostTools.set(spec.session_id, pending = new Set());
            const operation = this.transport.request('mj/tool.dispatch', call);
            pending.add(operation);
            try { return await operation; }
            finally { pending.delete(operation); if (!pending.size) this.hostTools.delete(spec.session_id); }
          } });
      }
      agentCtx.tools.guard(exec => exec.agent !== agent || !allowed.has(exec.name) ? 'Financial role has no authority for this tool' : undefined);
      agentCtx.on('agent/pre-step', async (payload, next) => {
        if (++steps > spec.max_steps) { payload.agent.cancel({ kind: 'hook', reason: 'MingJing step budget exhausted' }); return { kind: 'reject' }; }
        return next();
      });
      agentCtx.on('agent/request', async (_payload, next) => {
        const call = await next();
        return spec.route.temperature == null ? call : { ...call, temperature: spec.route.temperature };
      });
    };
    const options = { agentOptions, setup };
    const handle = resume ? await this.ctx.agents.resume({ ...options, resumeSessionId: brandString(spec.session_id) })
      : await this.ctx.agents.create({ ...options, sessionId: brandString(spec.session_id),
        // Financial tools use resource IDs rather than filesystem cwd. Avoid
        // duplicating a long Windows home inside the persistence project key.
        // An explicit trusted cwd remains supported for controlled consumers.
        meta: spec.cwd === undefined ? {} : { cwd: resolve(spec.cwd) } });
    if (!resume) {
      await mkdir(descriptorDir, { recursive: true });
      try { await writeFile(descriptor, JSON.stringify(spec, null, 2), { encoding: 'utf8', flag: 'wx' }); }
      catch (error) { await handle.dispose(); throw error; }
    }
    this.records.set(spec.session_id, { spec, handle });
    return { session_id: spec.session_id, native_loop: true, resumed: resume, role: spec.role, provider: spec.route.provider, model: spec.route.model };
  }

  record(id) { const record = this.records.get(text(id, 'session_id')); if (!record) throw new Error('Session is not active'); return record; }

  async prompt(params) {
    const record = this.record(params.session_id ?? params.sessionId);
    if (record.handle.agent.status !== 'idle') throw new Error('Session is already running');
    const content = params.contentBlocks ?? [{ type: 'text', text: text(params.text, 'text') }];
    if (!Array.isArray(content) || content.some(block => block.type !== 'text' || typeof block.text !== 'string')) throw new TypeError('Financial prompts accept text blocks only');
    const message = createUserMessage({ content, source: { kind: 'user' } });
    record.handle.agent.followup(message);
    return { messageId: message.id, session_id: record.spec.session_id };
  }

  async cancel(params) {
    const record = this.record(params.session_id);
    record.handle.agent.cancel({ kind: 'user' }, { keepInbox: Boolean(params.keep_inbox) });
    await record.handle.agent.whenIdle();
    await this.awaitHostTools(record.spec.session_id);
    return { session_id: record.spec.session_id, cancelled: true, quiescent: true };
  }

  async closeSession(params) {
    const record = this.record(params.session_id);
    await record.handle.dispose();
    await this.awaitHostTools(record.spec.session_id);
    this.records.delete(record.spec.session_id);
    return { session_id: record.spec.session_id, closed: true, quiescent: true };
  }

  async close() {
    this.closing = true;
    await Promise.allSettled([...this.pending.values()]);
    const records = [...this.records.values()];
    this.records.clear();
    await Promise.allSettled(records.map(record => record.handle.dispose()));
    await Promise.allSettled(records.map(record => this.awaitHostTools(record.spec.session_id)));
    this.disposers.splice(0).forEach(dispose => dispose());
    await Promise.allSettled(this.adapters.splice(0).map(fiber => fiber.dispose()));
    await this.routeProxy.close();
    this.references.clear();
  }

  async request(method, params = {}) {
    switch (method) {
      case 'initialize': return { serverInfo: { name: 'deepseek-harness-sdk-runtime', version: VERSION }, ...this.describe() };
      case 'mj/runtime.describe': return this.describe();
      case 'mj/session.open': return await this.create(params);
      case 'mj/session.resume': return await this.create(params, true);
      case 'mj/session.prompt': case 'session/prompt': return await this.prompt(params);
      case 'mj/session.cancel': return await this.cancel(params);
      case 'mj/session.close': return await this.closeSession(params);
      case 'shutdown': await this.close(); return {};
      default: throw new Error('Unknown MingJing runtime method');
    }
  }
}

/** Serve only stdio RPC; stdout never contains logs or credentials. */
export function apply(ctx) {
  const transport = new JsonRpcLineTransport(process.stdin, process.stdout);
  const runtime = new FinancialRuntime(ctx, transport);
  let exiting;
  transport.onRequest(async (method, params) => {
    if (method === 'initialize' || method === 'mj/runtime.describe') await ctx.get('loader')?.await();
    const value = await runtime.request(method, params);
    if (method === 'shutdown') setImmediate(() => {
      exiting ??= (async () => { await transport.flush(); await ctx.root.fiber.dispose(); process.exit(0); })();
    });
    return value;
  });
  ctx.effect(() => { transport.start(); return async () => { await runtime.close(); transport.close(); }; }, 'mingjing.financialRpc');
}
