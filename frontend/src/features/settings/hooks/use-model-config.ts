// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useState, useEffect, useCallback } from 'react';
import { authFetch } from '@/services/api-client';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import { showToast } from '@/components/Toast';
import { t } from '@/i18n';
import { TEST_BUTTON_MIN_SPIN_MS } from '@/constants/timing';
import { useServiceHealth } from '@/hooks/use-service-health';

export const DEFAULT_NER_BACKEND_URL = 'http://127.0.0.1:8080/v1';

function normalizeServiceLive(
  status: 'online' | 'offline' | 'checking' | 'busy' | 'degraded' | undefined,
) {
  return status === 'online' || status === 'offline' ? status : undefined;
}

export type TextModelTab = 'local' | 'remote';

/** 一份文本模型连接配置。本地与远端字段一致，远端通常需要 API Key。 */
export interface TextModelProfileForm {
  base_url: string;
  model_name: string;
  display_name: string;
  api_key: string;
}

export interface TextModelRuntimeState {
  active: TextModelTab;
  local: TextModelProfileForm;
  remote: TextModelProfileForm;
}

function emptyProfile(baseUrl = ''): TextModelProfileForm {
  return { base_url: baseUrl, model_name: '', display_name: '', api_key: '' };
}

function emptyRuntimeState(): TextModelRuntimeState {
  return {
    active: 'local',
    local: emptyProfile(DEFAULT_NER_BACKEND_URL),
    remote: emptyProfile(),
  };
}

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === 'string' ? value : '';
}

function normalizeProfile(raw: unknown, fallbackUrl: string): TextModelProfileForm {
  if (!raw || typeof raw !== 'object') return emptyProfile(fallbackUrl);
  const source = raw as Record<string, unknown>;
  return {
    base_url: readString(source, 'base_url').trim() || fallbackUrl,
    model_name: readString(source, 'model_name'),
    display_name: readString(source, 'display_name'),
    api_key: readString(source, 'api_key'),
  };
}

function normalizeRuntimeState(value: unknown): TextModelRuntimeState {
  const data = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const legacyUrl = readString(data, 'llamacpp_base_url').trim();
  return {
    active: data.active === 'remote' ? 'remote' : 'local',
    local: normalizeProfile(data.local, legacyUrl || DEFAULT_NER_BACKEND_URL),
    remote: normalizeProfile(data.remote, ''),
  };
}

/** 主页 / 侧栏展示名：显式显示名优先，其次模型名。 */
export function profileDisplayName(profile: TextModelProfileForm): string {
  return (profile.display_name || profile.model_name || '').trim();
}

export function useNerBackend() {
  const [tab, setTab] = useState<TextModelTab>('local');
  const [form, setForm] = useState<TextModelRuntimeState>(emptyRuntimeState);
  const [saved, setSaved] = useState<TextModelRuntimeState>(emptyRuntimeState);
  const [nerLoading, setNerLoading] = useState(true);
  const [nerSaving, setNerSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [nerLive, setNerLive] = useState<'online' | 'offline' | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { health, refresh } = useServiceHealth();

  const fetchNerBackend = useCallback(async () => {
    try {
      setNerLoading(true);
      setLoadError(null);
      const res = await fetchWithTimeout('/api/v1/ner-backend', { timeoutMs: 25000 });
      if (!res.ok) throw new Error('fetch failed');
      const state = normalizeRuntimeState(await res.json().catch(() => ({})));
      setSaved(state);
      setForm(state);
      setTab(state.active);
    } catch (e) {
      if (import.meta.env.DEV) console.error('fetch NER config failed', e);
      setLoadError(t('settings.loadFailed'));
    } finally {
      setNerLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchNerBackend();
  }, [fetchNerBackend]);

  useEffect(() => {
    const status = health?.services?.has_ner?.status;
    setNerLive(normalizeServiceLive(status));
  }, [health]);

  const activeProfile = form[tab];

  const updateActiveProfile = useCallback(
    (patch: Partial<TextModelProfileForm>) => {
      setForm((current) => ({ ...current, [tab]: { ...current[tab], ...patch } }));
    },
    [tab],
  );

  // 保存与测试都提交两份配置，只用 active 指向当前页签：另一份不会被清空。
  const payload = useCallback(
    () => ({ active: tab, local: form.local, remote: form.remote }),
    [tab, form],
  );

  const saveNerBackend = useCallback(async () => {
    try {
      setNerSaving(true);
      setTestResult(null);
      const res = await authFetch('/api/v1/ner-backend', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        showToast((d as { detail?: string }).detail || t('settings.saveFailed'), 'error');
        return;
      }
      const state = normalizeRuntimeState(await res.json().catch(() => ({})));
      setSaved(state);
      setForm(state);
      // 主页 / 侧栏的“本地服务”名称读的是健康接口，保存后刷新一次即可看到新名字。
      refresh();
      setTestResult({
        success: true,
        message: t('settings.textModel.saveApplied').replace(
          '{model}',
          profileDisplayName(state[state.active]) || t('settings.textModel.unnamedModel'),
        ),
      });
    } catch (e) {
      if (import.meta.env.DEV) console.error(e);
      showToast(t('settings.saveFailed'), 'error');
    } finally {
      setNerSaving(false);
    }
  }, [payload, refresh]);

  const testConnection = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await authFetch('/api/v1/ner-backend/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      let data: { success?: boolean; message?: string; detail?: unknown } = {};
      try {
        data = await res.json();
      } catch {
        setTestResult({
          success: false,
          message: t('settings.textModel.responseNotJson').replace('{status}', String(res.status)),
        });
        return;
      }
      if (!res.ok) {
        const d = data.detail;
        let errMsg = t('settings.textModel.requestFailedWithStatus').replace(
          '{status}',
          String(res.status),
        );
        if (Array.isArray(d))
          errMsg = d.map((x: { msg?: string }) => x.msg || JSON.stringify(x)).join('\uFF1B');
        else if (typeof d === 'string') errMsg = d;
        else if (d && typeof d === 'object' && 'msg' in (d as object))
          errMsg = String((d as { msg: string }).msg);
        setTestResult({ success: false, message: errMsg });
        return;
      }
      setTestResult({
        success: Boolean(data.success),
        message:
          data.message ||
          (data.success
            ? t('settings.textModel.connectSuccess')
            : t('settings.textModel.connectFailed')),
      });
    } catch {
      setTestResult({ success: false, message: t('settings.textModel.testRequestFailed') });
    } finally {
      setTimeout(() => setTesting(false), TEST_BUTTON_MIN_SPIN_MS);
    }
  }, [payload]);

  const clearNerOverride = useCallback(async () => {
    try {
      const res = await authFetch('/api/v1/ner-backend', { method: 'DELETE' });
      if (res.ok) {
        await fetchNerBackend();
        setTestResult({ success: true, message: t('settings.textModel.resetSuccess') });
      } else {
        setTestResult({ success: false, message: t('settings.textModel.resetFailed') });
      }
    } catch (e) {
      if (import.meta.env.DEV) console.error(e);
      setTestResult({ success: false, message: t('settings.textModel.resetFailed') });
    }
  }, [fetchNerBackend]);

  return {
    tab,
    setTab,
    activeTab: saved.active,
    activeProfile,
    form,
    saved,
    updateActiveProfile,
    nerLoading,
    nerSaving,
    testing,
    testResult,
    nerLive,
    loadError,
    fetchNerBackend,
    saveNerBackend,
    testConnection,
    clearNerOverride,
  };
}

export interface ModelConfig {
  id: string;
  name: string;
  provider: 'local' | 'openai' | 'custom';
  enabled: boolean;
  base_url?: string;
  api_key?: string;
  model_name: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  enable_thinking: boolean;
  description?: string;
}

interface ModelConfigList {
  configs: ModelConfig[];
  active_id?: string;
}
interface BuiltinServiceLive {
  paddle?: 'online' | 'offline';
  visual_features?: 'online' | 'offline';
}

export const BUILTIN_VISION_IDS = new Set(['paddle_ocr_service', 'visual_features_service']);

export const DEFAULT_MODEL_FORM: Partial<ModelConfig> = {
  provider: 'local',
  temperature: 0.8,
  top_p: 0.6,
  max_tokens: 4096,
  enable_thinking: false,
};

export function useVisionModelConfig() {
  const [modelConfigs, setModelConfigs] = useState<ModelConfigList>({
    configs: [],
    active_id: undefined,
  });
  const [loading, setLoading] = useState(true);
  const [builtinLive, setBuiltinLive] = useState<BuiltinServiceLive | null>(null);
  const [testingModelId, setTestingModelId] = useState<string | null>(null);
  const [settingActiveModelId, setSettingActiveModelId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { health } = useServiceHealth();

  const fetchModelConfigs = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const res = await fetchWithTimeout('/api/v1/model-config', { timeoutMs: 25000 });
      if (!res.ok) throw new Error('fetch failed');
      const data = await res.json().catch(() => ({}));
      const rawConfigs: ModelConfig[] = Array.isArray(data?.configs) ? data.configs : [];
      // After fetching models from API, redact keys
      const configs = rawConfigs.map((m) => ({
        ...m,
        api_key: m.api_key ? '__REDACTED__' : undefined,
      }));
      setModelConfigs({
        configs,
        active_id: typeof data?.active_id === 'string' ? data.active_id : undefined,
      });
    } catch (err) {
      if (import.meta.env.DEV) console.error('fetch model configs failed', err);
      setLoadError(t('settings.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchModelConfigs();
  }, [fetchModelConfigs]);

  useEffect(() => {
    setBuiltinLive({
      paddle: normalizeServiceLive(health?.services?.paddle_ocr?.status),
      visual_features: normalizeServiceLive(health?.services?.visual_features?.status),
    });
  }, [health]);

  const saveModelConfig = useCallback(
    async (form: Partial<ModelConfig>, editingId: string | null) => {
      if (!form.name || !form.model_name) return false;
      const configId = editingId || `custom_${Date.now()}`;
      // In save payload, omit redacted/empty keys so the backend keeps the existing key
      const sanitizedForm = { ...form };
      if (sanitizedForm.api_key === '__REDACTED__' || sanitizedForm.api_key === '') {
        delete sanitizedForm.api_key;
      }
      const payload = {
        ...sanitizedForm,
        id: configId,
        enabled:
          editingId && BUILTIN_VISION_IDS.has(editingId) ? true : (sanitizedForm.enabled ?? true),
      };
      const url = editingId ? `/api/v1/model-config/${editingId}` : '/api/v1/model-config';
      const method = editingId ? 'PUT' : 'POST';
      const res = await authFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        await fetchModelConfigs();
        return true;
      }
      const data = await res.json();
      showToast(data.detail || t('settings.saveFailed'), 'error');
      return false;
    },
    [fetchModelConfigs],
  );

  const deleteModelConfig = useCallback(
    async (configId: string) => {
      const res = await authFetch(`/api/v1/model-config/${configId}`, { method: 'DELETE' });
      if (res.ok) await fetchModelConfigs();
      else {
        const d = await res.json();
        showToast(d.detail || t('settings.deleteTypeFailed'), 'error');
      }
    },
    [fetchModelConfigs],
  );

  const testModelConfig = useCallback(async (configId: string) => {
    setTestingModelId(configId);
    setTestResult(null);
    try {
      const timeoutMs = configId === 'paddle_ocr_service' ? 60000 : 15000;
      const res = await fetchWithTimeout(`/api/v1/model-config/test/${configId}`, {
        method: 'POST',
        timeoutMs,
      });
      setTestResult(await res.json());
    } catch {
      setTestResult({
        success: false,
        message:
          configId === 'paddle_ocr_service'
            ? t('settings.visionModel.testFailedLong')
            : t('settings.visionModel.testFailed'),
      });
    } finally {
      setTestingModelId(null);
    }
  }, []);

  const resetModelConfigs = useCallback(async () => {
    const res = await authFetch('/api/v1/model-config/reset', { method: 'POST' });
    if (res.ok) await fetchModelConfigs();
  }, [fetchModelConfigs]);

  const setActiveModelConfig = useCallback(
    async (configId: string) => {
      setSettingActiveModelId(configId);
      try {
        const res = await authFetch(`/api/v1/model-config/active/${configId}`, { method: 'POST' });
        if (res.ok) {
          await fetchModelConfigs();
          return true;
        }
        const data = await res.json().catch(() => ({}));
        showToast(
          (data as { detail?: string }).detail || t('settings.visionModel.setActiveFailed'),
          'error',
        );
        return false;
      } catch (err) {
        if (import.meta.env.DEV) console.error('set active model config failed', err);
        showToast(t('settings.visionModel.setActiveFailed'), 'error');
        return false;
      } finally {
        setSettingActiveModelId(null);
      }
    },
    [fetchModelConfigs],
  );

  const liveForBuiltin = useCallback(
    (configId: string): 'online' | 'offline' | undefined => {
      if (configId === 'paddle_ocr_service') return builtinLive?.paddle;
      if (configId === 'visual_features_service') return builtinLive?.visual_features;
      return undefined;
    },
    [builtinLive],
  );

  const getProviderLabel = (provider: string) => {
    switch (provider) {
      case 'local':
        return t('settings.visionModel.tag.local');
      case 'openai':
        return 'OpenAI';
      case 'custom':
        return t('settings.visionModel.tag.custom');
      default:
        return provider;
    }
  };

  return {
    modelConfigs,
    loading,
    builtinLive,
    testingModelId,
    settingActiveModelId,
    testResult,
    loadError,
    fetchModelConfigs,
    saveModelConfig,
    deleteModelConfig,
    testModelConfig,
    resetModelConfigs,
    setActiveModelConfig,
    liveForBuiltin,
    getProviderLabel,
  };
}
