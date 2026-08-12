import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle
} from '@/components/ui/AlertDialog'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { useSettingsStore } from '@/stores/settings'
import { useBackendState } from '@/stores/state'
import { InvalidApiKeyError, RequireApiKeError } from '@/api/weave'

interface ApiKeyAlertProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const ApiKeyAlert = ({ open: opened, onOpenChange: setOpened }: ApiKeyAlertProps) => {
  const { t } = useTranslation()
  const apiKey = useSettingsStore.use.apiKey()
  const [tempApiKey, setTempApiKey] = useState<string>('')
  const message = useBackendState.use.message()

  // Seed the draft from the stored key, and re-seed when the key changes or the
  // dialog reopens. Adjusted during render rather than in an effect: as an
  // effect this painted the field with the *previous* key for one frame each
  // time the dialog opened. React re-runs the component before committing, so
  // that frame no longer exists.
  const [seededFrom, setSeededFrom] = useState<string>(`${apiKey ?? ''}|${opened}`)
  const seedKey = `${apiKey ?? ''}|${opened}`
  if (seededFrom !== seedKey) {
    setSeededFrom(seedKey)
    setTempApiKey(apiKey || '')
  }

  useEffect(() => {
    if (message) {
      if (message.includes(InvalidApiKeyError) || message.includes(RequireApiKeError)) {
        setOpened(true)
      }
    }
  }, [message, setOpened])

  const setApiKey = useCallback(() => {
    useSettingsStore.setState({ apiKey: tempApiKey || null })
    setOpened(false)
  }, [tempApiKey, setOpened])

  const handleTempApiKeyChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setTempApiKey(e.target.value)
    },
    [setTempApiKey]
  )

  return (
    <AlertDialog open={opened} onOpenChange={setOpened}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('apiKeyAlert.title')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('apiKeyAlert.description')}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-4">
          <form className="flex gap-2" onSubmit={(e) => e.preventDefault()}>
            <Input
              type="password"
              value={tempApiKey}
              onChange={handleTempApiKeyChange}
              placeholder={t('apiKeyAlert.placeholder')}
              className="max-h-full w-full min-w-0"
              autoComplete="off"
            />

            <Button onClick={setApiKey} variant="outline" size="sm">
              {t('apiKeyAlert.save')}
            </Button>
          </form>
          {message && (
            <div className="text-sm text-red-500">
              {message}
            </div>
          )}
        </div>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export default ApiKeyAlert
