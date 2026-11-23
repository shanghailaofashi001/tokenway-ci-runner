#!/usr/bin/env python3
"""Add the TokenWay file library UI and persistent storage deployment defaults."""
from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    if new in content:
        return
    if old not in content:
        raise SystemExit(f"{path}: insertion anchor missing ({label})")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


def replace_if_present(path: str, old: str, new: str, label: str) -> None:
    if (ROOT / path).is_file():
        replace_once(path, old, new, label)
    else:
        print(f"SKIP optional deployment overlay: {path} is not part of this source snapshot")


component = r'''/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.
*/
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, File, FolderOpen, RefreshCw, Trash2, Upload } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'

type UserFile = {
  id: string
  object: 'file'
  bytes: number
  created_at: number
  filename: string
  purpose: string
  status: string
}

const FILES_QUERY_KEY = ['user-files']

async function listUserFiles(): Promise<UserFile[]> {
  const response = await api.get('/api/user/files', { params: { limit: 100 } })
  if (response.data?.object !== 'list' || !Array.isArray(response.data?.data)) {
    throw new Error('Invalid file library response')
  }
  return response.data.data as UserFile[]
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) return error.message
  return fallback
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function FileLibraryCard() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const filesQuery = useQuery({
    queryKey: FILES_QUERY_KEY,
    queryFn: listUserFiles,
  })

  const uploadMutation = useMutation({
    mutationFn: async (file: globalThis.File) => {
      const form = new FormData()
      form.append('purpose', 'user_data')
      form.append('file', file, file.name)
      const response = await api.post('/api/user/files', form)
      if (response.data?.object !== 'file' || !response.data?.id) {
        throw new Error('Invalid file upload response')
      }
      return response.data as UserFile
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: FILES_QUERY_KEY })
      toast.success(t('File uploaded successfully'))
    },
    onError: (error) => toast.error(getErrorMessage(error, t('File upload failed'))),
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const response = await api.delete(`/api/user/files/${encodeURIComponent(id)}`)
      if (response.data?.deleted !== true) throw new Error('File deletion was not confirmed')
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: FILES_QUERY_KEY })
      toast.success(t('File deleted'))
    },
    onError: (error) => toast.error(getErrorMessage(error, t('File deletion failed'))),
  })

  const downloadFile = async (file: UserFile) => {
    try {
      const response = await api.get<Blob>(
        `/api/user/files/${encodeURIComponent(file.id)}/content`,
        { responseType: 'blob' }
      )
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = file.filename
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      toast.error(getErrorMessage(error, t('File download failed')))
    }
  }

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    if (file.size > 512 * 1024 * 1024) {
      toast.error(t('Files must be 512 MiB or smaller'))
      return
    }
    uploadMutation.mutate(file)
  }

  let errorMessage = t('Unable to load files')
  if (filesQuery.error instanceof Error) errorMessage = filesQuery.error.message
  let refreshClassName = 'h-4 w-4'
  if (filesQuery.isFetching) refreshClassName += ' animate-spin'
  let uploadLabel = t('Upload file')
  if (uploadMutation.isPending) uploadLabel = t('Uploading')

  let fileContent: ReactNode
  if (filesQuery.isLoading) {
    fileContent = (
      <div className='py-8 text-center text-sm text-muted-foreground'>{t('Loading files...')}</div>
    )
  } else if (filesQuery.isError) {
    fileContent = (
      <div className='flex flex-col items-center gap-3 py-8 text-center'>
        <p className='text-sm text-destructive'>
          {errorMessage}
        </p>
        <Button type='button' variant='outline' onClick={() => void filesQuery.refetch()}>
          {t('Retry')}
        </Button>
      </div>
    )
  } else if (filesQuery.data && filesQuery.data.length > 0) {
    fileContent = (
      <ul className='divide-y'>
        {filesQuery.data.map((file) => (
          <li key={file.id} className='flex min-w-0 items-center gap-3 py-3 first:pt-0 last:pb-0'>
            <div className='flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted'>
              <File className='h-5 w-5 text-muted-foreground' />
            </div>
            <div className='min-w-0 flex-1'>
              <p className='truncate text-sm font-medium' title={file.filename}>{file.filename}</p>
              <p className='text-xs text-muted-foreground'>
                {formatBytes(file.bytes)} · {file.purpose} · {new Date(file.created_at * 1000).toLocaleString()}
              </p>
            </div>
            <Button type='button' variant='ghost' size='icon' aria-label={t('Download file')} onClick={() => void downloadFile(file)}>
              <Download className='h-4 w-4' />
            </Button>
            <Button
              type='button'
              variant='ghost'
              size='icon'
              aria-label={t('Delete file')}
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate(file.id)}
            >
              <Trash2 className='h-4 w-4 text-destructive' />
            </Button>
          </li>
        ))}
      </ul>
    )
  } else {
    fileContent = (
      <div className='flex flex-col items-center gap-2 py-8 text-center text-muted-foreground'>
        <FolderOpen className='h-9 w-9 opacity-60' />
        <p className='text-sm'>{t('Your file library is empty')}</p>
        <p className='text-xs'>{t('Uploaded files are private to your account')}</p>
      </div>
    )
  }

  return (
    <Card data-card-hover='false' className='gap-0 overflow-hidden'>
      <CardHeader className='flex flex-row items-start justify-between gap-3 border-b'>
        <div className='min-w-0 space-y-1'>
          <CardTitle className='flex items-center gap-2 text-base'>
            <FolderOpen className='h-4 w-4' />
            {t('File library')}
          </CardTitle>
          <CardDescription>
            {t('Upload, download and manage files private to your account')}
          </CardDescription>
        </div>
        <div className='flex shrink-0 items-center gap-2'>
          <Button
            type='button'
            variant='outline'
            size='icon'
            aria-label={t('Refresh files')}
            onClick={() => void filesQuery.refetch()}
            disabled={filesQuery.isFetching}
          >
            <RefreshCw className={refreshClassName} />
          </Button>
          <Button
            type='button'
            onClick={() => inputRef.current?.click()}
            disabled={uploadMutation.isPending}
          >
            <Upload className='mr-2 h-4 w-4' />
            {uploadLabel}
          </Button>
        </div>
      </CardHeader>
      <CardContent className='space-y-3 p-3 sm:p-5'>
        <input
          ref={inputRef}
          type='file'
          className='hidden'
          onChange={handleFileChange}
          aria-label={t('Choose a file to upload')}
        />
        {fileContent}
        <p className='text-xs text-muted-foreground'>{t('Maximum file size: 512 MiB')}</p>
      </CardContent>
    </Card>
  )
}
'''
target = ROOT / "web/src/features/profile/components/file-library-card.tsx"
if target.exists():
    if target.read_text(encoding="utf-8") != component:
        raise SystemExit("web file library component already exists with unexpected contents")
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(component, encoding="utf-8")

replace_once(
    "web/src/features/profile/index.tsx",
    "import { CheckinCalendarCard } from './components/checkin-calendar-card'\n",
    "import { CheckinCalendarCard } from './components/checkin-calendar-card'\nimport { FileLibraryCard } from './components/file-library-card'\n",
    "profile file library import",
)
replace_once(
    "web/src/features/profile/index.tsx",
    "                <ProfileSettingsCard\n                  profile={profile}\n                  loading={loading}\n                  onProfileUpdate={refreshProfile}\n                  quotaRemindEnabled={quotaRemindEnabled}\n                  quotaRemindThreshold={quotaRemindThreshold}\n                />\n",
    "                <ProfileSettingsCard\n                  profile={profile}\n                  loading={loading}\n                  onProfileUpdate={refreshProfile}\n                  quotaRemindEnabled={quotaRemindEnabled}\n                  quotaRemindThreshold={quotaRemindThreshold}\n                />\n                <FileLibraryCard />\n",
    "profile file library placement",
)
replace_if_present(
    "docker-compose.yml",
    "      - TZ=Asia/Shanghai\n",
    "      - TZ=Asia/Shanghai\n      - TOKENWAY_FILE_STORAGE_DIR=/data/files\n",
    "persistent file library storage root",
)
file_doc = ROOT / "docs/TOKENWAY-FILE-LIBRARY-DEPLOYMENT.md"
if not file_doc.exists():
    file_doc.parent.mkdir(parents=True, exist_ok=True)
    file_doc.write_text(
        """# TokenWay File Library Storage

The file library stores metadata in the application database and file bytes in the
filesystem directory configured by `TOKENWAY_FILE_STORAGE_DIR`.

For the provided single-node Docker Compose deployment, set:

```dotenv
TOKENWAY_FILE_STORAGE_DIR=/data/files
```

Mount a persistent volume over `/data` (the supplied Compose deployment already
uses the `tokenway_data` volume). Back up the database and this directory together.
Restore both from the same backup point to preserve metadata-to-object consistency.

The default application path is `data/files` relative to the process working
directory. The production deployment should set the absolute path above explicitly.

For multiple application replicas, all replicas must mount the same durable,
access-controlled shared filesystem. Separate per-container local directories are
not a supported shared-library configuration. Restrict directory permissions to
the application user and exclude this directory from public static file serving.
""",
        encoding="utf-8",
    )

print("PASS: web file library UI and persistent storage deployment settings applied")
