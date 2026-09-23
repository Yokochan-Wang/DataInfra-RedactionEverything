// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useT } from '@/i18n';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { tonePanelClass } from '@/utils/toneClasses';
import type { TextModelProfileForm } from '../hooks/use-model-config';

interface TextModelProfileCardProps {
  title: string;
  description: string;
  profile: TextModelProfileForm;
  onChange: (patch: Partial<TextModelProfileForm>) => void;
  onTest: () => void;
  onSave: () => void;
  testing: boolean;
  saving: boolean;
  isActive: boolean;
  liveStatus?: 'online' | 'offline';
  showApiKey?: boolean;
  endpointPlaceholder?: string;
}

export function TextModelProfileCard({
  title,
  description,
  profile,
  onChange,
  onTest,
  onSave,
  testing,
  saving,
  isActive,
  liveStatus,
  showApiKey = false,
  endpointPlaceholder,
}: TextModelProfileCardProps) {
  const t = useT();

  return (
    <Card className="overflow-hidden border-border/70 shadow-[var(--shadow-control)]">
      <CardHeader className="px-4 pb-2 pt-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-1">
            <CardTitle className="text-base">{title}</CardTitle>
            <CardDescription className="max-w-3xl text-xs leading-5">{description}</CardDescription>
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {!isActive && (
                <Badge variant="outline" className="whitespace-nowrap">
                  {t('settings.textModel.notActiveBadge')}
                </Badge>
              )}
              {/* 健康检查探的是"当前生效"那套配置，未启用的页签不显示它的在线状态。 */}
              {isActive && liveStatus === 'online' && (
                <Badge className={cn('whitespace-nowrap', tonePanelClass.success)}>
                  {t('common.online')}
                </Badge>
              )}
              {isActive && liveStatus === 'offline' && (
                <Badge variant="destructive" className="whitespace-nowrap">
                  {t('common.offline')}
                </Badge>
              )}
              {isActive && liveStatus === undefined && (
                <Badge variant="outline" className="whitespace-nowrap">
                  {t('common.checking')}
                </Badge>
              )}
            </div>
          </div>
          <Button
            size="sm"
            className="h-8 shrink-0 whitespace-nowrap"
            onClick={onSave}
            disabled={saving}
            data-testid="text-model-save"
          >
            {saving ? t('common.saving') : t('settings.textModel.saveApply')}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-3 px-4 pb-4 pt-0">
        <div className="space-y-1">
          <Label className="text-xs">{t('settings.textModel.endpointLabel')}</Label>
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              value={profile.base_url}
              onChange={(e) => onChange({ base_url: e.target.value })}
              placeholder={endpointPlaceholder ?? 'http://127.0.0.1:8080/v1'}
              className="h-9 min-w-0 flex-1 font-mono text-sm"
              data-testid="text-model-endpoint"
            />
            <Button
              size="sm"
              variant="outline"
              className="h-9 shrink-0 whitespace-nowrap"
              onClick={onTest}
              disabled={testing}
              data-testid="text-model-test"
            >
              {testing ? t('common.testing') : t('common.test')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t('settings.textModel.endpointHint')}</p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label className="text-xs">{t('settings.textModel.modelNameLabel')}</Label>
            <Input
              value={profile.model_name}
              onChange={(e) => onChange({ model_name: e.target.value })}
              placeholder={t('settings.textModel.modelNamePlaceholder')}
              className="h-9 font-mono text-sm"
              data-testid="text-model-name"
            />
            <p className="text-xs text-muted-foreground">{t('settings.textModel.modelNameHint')}</p>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t('settings.textModel.displayNameLabel')}</Label>
            <Input
              value={profile.display_name}
              onChange={(e) => onChange({ display_name: e.target.value })}
              placeholder={t('settings.textModel.displayNamePlaceholder')}
              className="h-9 text-sm"
              data-testid="text-model-display-name"
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.textModel.displayNameHint')}
            </p>
          </div>
        </div>

        {showApiKey && (
          <div className="space-y-1">
            <Label className="text-xs">{t('settings.textModel.apiKeyLabel')}</Label>
            <Input
              value={profile.api_key}
              onChange={(e) => onChange({ api_key: e.target.value })}
              placeholder={t('settings.textModel.apiKeyPlaceholder')}
              className="h-9 font-mono text-sm"
              autoComplete="off"
              data-testid="text-model-api-key"
            />
            <p className="text-xs text-muted-foreground">{t('settings.textModel.apiKeyHint')}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
