// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, RotateCcw } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { getEntityTypeName, normalizeEntityTypeId } from '@/config/entityTypes';
import { useT } from '@/i18n';
import { downloadFile } from '@/services/api-client';
import { fileApi, restorationApi, type RestoreCategoriesResult } from '@/services/api';
import type { FileListItem, FileInfo } from '@/types';
import { cn } from '@/lib/utils';

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

export function Restoration() {
  const t = useT();
  const [files, setFiles] = useState<FileListItem[]>([]);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FileInfo | null>(null);
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [result, setResult] = useState<RestoreCategoriesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fileApi
      .list(1, 100)
      .then((response) => {
        if (cancelled) return;
        const redacted = (response.files || []).filter((item) => item.has_output);
        setFiles(redacted);
        setSelectedFileId(redacted[0]?.file_id ?? null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : t('restoration.loadFailed'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  const selectFile = useCallback(async (fileId: string) => {
    setSelectedFileId(fileId);
    setSelectedTypes(new Set());
    setResult(null);
    setError(null);
    setDetailLoading(true);
    try {
      setDetail(await fileApi.getInfo(fileId));
    } catch (err: unknown) {
      setDetail(null);
      setError(err instanceof Error ? err.message : t('restoration.loadFailed'));
    } finally {
      setDetailLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (selectedFileId && !detail && !detailLoading) {
      void selectFile(selectedFileId);
    }
  }, [detail, detailLoading, selectFile, selectedFileId]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entity of detail?.entities || []) {
      if (entity.selected === false) continue;
      const typeId = normalizeEntityTypeId(entity.type);
      counts.set(typeId, (counts.get(typeId) || 0) + 1);
    }
    for (const boxes of Object.values(detail?.bounding_boxes || {})) {
      for (const box of boxes) {
        if (box.selected === false) continue;
        const typeId = normalizeEntityTypeId(box.type);
        counts.set(typeId, (counts.get(typeId) || 0) + 1);
      }
    }
    return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [detail]);

  const selectedFile = files.find((item) => item.file_id === selectedFileId) || null;

  const toggleCategory = (typeId: string, checked: boolean) => {
    setSelectedTypes((current) => {
      const next = new Set(current);
      if (checked) next.add(typeId);
      else next.delete(typeId);
      return next;
    });
  };

  const restore = async () => {
    if (!selectedFileId || selectedTypes.size === 0) return;
    setRestoring(true);
    setError(null);
    try {
      setResult(await restorationApi.restore(selectedFileId, [...selectedTypes]));
    } catch (err: unknown) {
      setResult(null);
      setError(err instanceof Error ? err.message : t('restoration.failed'));
    } finally {
      setRestoring(false);
    }
  };

  const download = async () => {
    if (!result) return;
    try {
      await downloadFile(result.download_url, result.output_filename);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('restoration.downloadFailed'));
    }
  };

  return (
    <div className="saas-page flex min-h-0 flex-1 flex-col overflow-hidden bg-background" data-testid="restoration-page">
      <div className="page-shell !max-w-[min(100%,1800px)] !px-3 !py-3 sm:!px-4">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{t('page.restoration.title')}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{t('page.restoration.sub')}</p>
          </div>
        </div>

        {error ? (
          <Alert variant="destructive" className="mb-3">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[360px_minmax(0,1fr)]">
          <Card className="page-surface overflow-hidden rounded-[20px]">
            <CardContent className="p-0">
              <div className="border-b border-border/70 px-4 py-3 text-sm font-semibold">
                {t('restoration.redactedFiles')}
              </div>
              <div className="max-h-[calc(100dvh-260px)] overflow-y-auto">
                {loading ? (
                  <div className="space-y-2 p-4">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-12 w-full" />
                  </div>
                ) : files.length === 0 ? (
                  <p className="p-4 text-sm text-muted-foreground">{t('restoration.noFiles')}</p>
                ) : (
                  files.map((item) => (
                    <button
                      key={item.file_id}
                      type="button"
                      onClick={() => void selectFile(item.file_id)}
                      className={cn(
                        'block w-full border-b border-border/50 px-4 py-3 text-left transition-colors hover:bg-accent',
                        item.file_id === selectedFileId && 'bg-accent',
                      )}
                    >
                      <span className="block truncate text-sm font-medium">{item.original_filename}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {formatBytes(item.file_size)} · {item.created_at || '-'}
                      </span>
                    </button>
                  ))
                )}
              </div>
            </CardContent>
          </Card>

          <Card className="page-surface overflow-hidden rounded-[20px]">
            <CardContent className="p-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold">
                    {selectedFile?.original_filename || t('restoration.selectFile')}
                  </h2>
                  <p className="mt-1 text-xs text-muted-foreground">{t('restoration.categoryHint')}</p>
                </div>
                <Button
                  onClick={() => void restore()}
                  disabled={!selectedFileId || selectedTypes.size === 0 || restoring || detailLoading}
                  data-testid="restore-categories-button"
                >
                  <RotateCcw className="size-4" />
                  {restoring ? t('restoration.restoring') : t('restoration.restoreSelected')}
                </Button>
              </div>

              <div className="grid gap-3 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                <div className="space-y-2">
                  {detailLoading ? (
                    <div className="space-y-2">
                      <Skeleton className="h-10 w-full" />
                      <Skeleton className="h-10 w-full" />
                    </div>
                  ) : categoryCounts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t('restoration.noCategories')}</p>
                  ) : (
                    categoryCounts.map(([typeId, count]) => (
                      <label
                        key={typeId}
                        className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-border/70 px-3 py-2.5"
                      >
                        <span className="flex min-w-0 items-center gap-3">
                          <Checkbox
                            checked={selectedTypes.has(typeId)}
                            onCheckedChange={(checked) => toggleCategory(typeId, checked === true)}
                          />
                          <span className="truncate text-sm">{getEntityTypeName(typeId)}</span>
                        </span>
                        <Badge variant="secondary">{count}</Badge>
                      </label>
                    ))
                  )}
                </div>

                <div className="rounded-xl border border-border/70 bg-accent/30 p-4">
                  <h3 className="text-sm font-semibold">{t('restoration.resultTitle')}</h3>
                  {result ? (
                    <div className="mt-3 space-y-3 text-sm">
                      <p>
                        {t('restoration.restoredCount').replace(
                          '{n}',
                          String(result.restored_entity_count),
                        )}
                      </p>
                      <p>
                        {t('restoration.retainedCount').replace(
                          '{n}',
                          String(result.retained_redaction_count),
                        )}
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {result.restored_categories.map((typeId) => (
                          <Badge key={typeId} variant="outline">{getEntityTypeName(typeId)}</Badge>
                        ))}
                      </div>
                      <Button className="w-full" variant="outline" onClick={() => void download()}>
                        <Download className="size-4" />
                        {t('restoration.download')}
                      </Button>
                    </div>
                  ) : (
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">
                      {t('restoration.resultEmpty')}
                    </p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
