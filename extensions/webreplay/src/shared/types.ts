/** 元素定位快照（录制时写入，重放时解析） */
export interface ElementSelector {
  css: string;
  xpath: string;
  textContent: string | null;
  tagName: string;
  attributes: Record<string, string>;
}

export type StepKind = 'click' | 'input' | 'key' | 'scroll' | 'wait';

export interface ClickStep {
  kind: 'click';
  selector: ElementSelector;
  recordedAt: number;
  frameUrl: string;
  originTabId?: number;
}

export interface InputStep {
  kind: 'input';
  selector: ElementSelector;
  value: string;
  control: 'value' | 'checked' | 'textContent';
  checked?: boolean;
  recordedAt: number;
  frameUrl: string;
  originTabId?: number;
}

export interface KeyStep {
  kind: 'key';
  selector: ElementSelector;
  key: 'Enter' | 'Escape';
  code: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  recordedAt: number;
  frameUrl: string;
  originTabId?: number;
}

export interface ScrollStep {
  kind: 'scroll';
  selector?: ElementSelector;
  x: number;
  y: number;
  recordedAt: number;
  frameUrl: string;
  originTabId?: number;
}

export interface WaitStep {
  kind: 'wait';
  reason: 'mutation-quiet' | 'fixed';
  timeoutMs: number;
  recordedAt?: number;
  frameUrl?: string;
  originTabId?: number;
}

export type ScriptStep = ClickStep | InputStep | KeyStep | ScrollStep | WaitStep;

/** 录制期间捕获的 HTTP 线索（简化版 apiChains） */
export interface ApiRequestHint {
  url: string;
  method: string;
  startedAt: number;
  status?: number;
  contentType?: string;
  bodySnippet?: string;
  responseSnippet?: string;
}

export interface Script {
  id: string;
  name: string;
  targetUrl: string;
  steps: ScriptStep[];
  apiHints?: ApiRequestHint[];
  schedule?: { timeOfDay: string };
  createdAt: number;
  updatedAt: number;
  runs?: RunRecord[];
}

export interface RunRecord {
  startedAt: number;
  endedAt: number;
  status: 'success' | 'failed' | 'aborted';
  error?: string;
  failedAtStep?: number;
}

export interface ScriptsStore {
  scripts: Script[];
}

export interface RecordingSession {
  draft: {
    scriptId: string;
    name: string;
    targetUrl: string;
    steps: ScriptStep[];
    startedAt: number;
  };
  tabId: number;
}

export interface ReplayState {
  script: Script;
  tabId: number;
  fromIndex: number;
  lastDoneStepIndex: number;
  startedAt: number;
  mode: 'dom';
}

export type BgMessage =
  | { type: 'rec/begin'; tabId: number; name: string }
  | { type: 'rec/end' }
  | { type: 'rec/cancel' }
  | { type: 'rec/state' }
  | { type: 'rec/step'; step: ScriptStep }
  | { type: 'rec/response-body'; url: string; method: string; startedAt: number; status: number; contentType?: string; bodyText?: string; bodyTruncated?: boolean; bodyOmitted?: string }
  | { type: 'rec/stop' }
  | { type: 'rec/start'; name: string; stepCount: number }
  | { type: 'rec/step-count'; count: number }
  | { type: 'replay/start'; script: Script; fromIndex: number }
  | { type: 'replay/abort' }
  | { type: 'replay/step-done'; index: number }
  | { type: 'replay/step-failed'; index: number; error: string }
  | { type: 'replay/complete' }
  | { type: 'replay/frame-switch'; index: number }
  | { type: 'state/query' }
  | { type: 'script/list' }
  | { type: 'script/get'; id: string }
  | { type: 'script/delete'; id: string }
  | { type: 'script/run'; id: string; params?: Record<string, string> }
  | { type: 'script/export-all' }
  | { type: 'script/import'; payload: ScriptsStore }
  | { type: 'run/abort' };
