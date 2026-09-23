// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useState } from 'react';
import { useT } from '@/i18n';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { InteractionLockOverlay } from '@/components/InteractionLockOverlay';
import { cn } from '@/lib/utils';
import { tonePanelClass } from '@/utils/toneClasses';
import { profileDisplayName, useNerBackend, type TextModelTab } from './hooks/use-model-config';
import { TextModelProfileCard } from './components/text-model-profile-card';

export function TextModel() {
  const t = useT();
  const [confirmResetOpen, setConfirmResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const {
    tab,
    setTab,
    activeTab,
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
  } = useNerBackend();

  if (nerLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-7 w-7 animate-spin rounded-full border-2 border-muted border-t-foreground" />
      </div>
    );
  }

  const tabLabel = (value: TextModelTab) =>
    value === 'remote' ? t('settings.textModel.tab.remote') : t('settings.textModel.tab.local');
  const activeName = profileDisplayName(activeProfile);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
      <div className="page-shell !max-w-[min(100%,1920px)] !px-3 !py-2 sm:!px-4 sm:!py-3">
        <div className="page-stack gap-3">
          {loadError && (
            <Alert variant="destructive" data-testid="text-model-load-error">
              <AlertDescription className="flex items-center justify-between gap-3">
                <span>{loadError}</span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 shrink-0"
                  onClick={() => void fetchNerBackend()}
                >
                  {t('common.retry')}
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <section className="surface-subtle flex shrink-0 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t('nav.textModel')}</h1>
              <p className="mt-0.5 max-w-5xl text-xs leading-5 text-muted-foreground">
                {t('settings.textModel.infoDesc')}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
              <div className="flex flex-wrap justify-end gap-1.5">
                <Badge
                  variant="outline"
                  className={cn(
                    'whitespace-nowrap',
                    activeTab === 'local' && tonePanelClass.success,
                  )}
                >
                  {t('settings.textModel.activeBadge')}
                  {tabLabel(activeTab)}
                </Badge>
                {activeName && (
                  <Badge variant="secondary" className="whitespace-nowrap font-mono">
                    {activeName}
                  </Badge>
                )}
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8 whitespace-nowrap"
                onClick={() => setConfirmResetOpen(true)}
                data-testid="reset-ner-default"
              >
                {t('settings.textModel.reset')}
              </Button>
            </div>
          </section>

          <Tabs
            value={tab}
            onValueChange={(value) => setTab(value === 'remote' ? 'remote' : 'local')}
            className="gap-3"
          >
            <TabsList className="rounded-xl border border-border/70 bg-muted/40 p-1">
              <TabsTrigger value="local" data-testid="text-model-tab-local">
                {t('settings.textModel.tab.local')}
              </TabsTrigger>
              <TabsTrigger value="remote" data-testid="text-model-tab-remote">
                {t('settings.textModel.tab.remote')}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="local" className="mt-0">
              <TextModelProfileCard
                title={t('settings.textModel.tab.local')}
                description={t('settings.textModel.localCardDescription')}
                profile={form.local}
                onChange={updateActiveProfile}
                onTest={() => void testConnection()}
                onSave={() => void saveNerBackend()}
                testing={testing}
                saving={nerSaving}
                isActive={activeTab === 'local'}
                liveStatus={nerLive}
                endpointPlaceholder="http://127.0.0.1:8080/v1"
              />
            </TabsContent>

            <TabsContent value="remote" className="mt-0">
              <TextModelProfileCard
                title={t('settings.textModel.tab.remote')}
                description={t('settings.textModel.remoteCardDescription')}
                profile={form.remote}
                onChange={updateActiveProfile}
                onTest={() => void testConnection()}
                onSave={() => void saveNerBackend()}
                testing={testing}
                saving={nerSaving}
                isActive={activeTab === 'remote'}
                liveStatus={nerLive}
                showApiKey
                endpointPlaceholder="http://gpu-host:8000/v1"
              />
            </TabsContent>
          </Tabs>

          {testResult && (
            <div
              className={cn(
                'shrink-0 rounded-lg border px-3 py-2 text-xs leading-5',
                testResult.success ? tonePanelClass.success : tonePanelClass.danger,
              )}
              data-testid="text-model-test-result"
            >
              {testResult.success ? '\u2713 ' : '\u2717 '}
              {testResult.message}
            </div>
          )}

          <p className="shrink-0 text-xs leading-5 text-muted-foreground">
            {t('settings.textModel.savedHint')
              .replace('{tab}', tabLabel(saved.active))
              .replace('{model}', profileDisplayName(saved[saved.active]) || t('settings.textModel.unnamedModel'))}
          </p>
        </div>
      </div>
      <ConfirmDialog
        open={confirmResetOpen}
        title={t('settings.textModel.reset')}
        message={t('settings.textModel.confirmClearOverride')}
        danger
        onConfirm={() => {
          void (async () => {
            setConfirmResetOpen(false);
            setResetting(true);
            try {
              await clearNerOverride();
            } finally {
              setResetting(false);
            }
          })();
        }}
        onCancel={() => setConfirmResetOpen(false)}
      />
      <InteractionLockOverlay active={nerSaving || testing || resetting} />
    </div>
  );
}
