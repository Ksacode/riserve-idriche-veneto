# Registra le attivita' pianificate di Windows per la raccolta automatica.
#   fiumi:            ogni ora
#   falde+invasi+bollettini: ogni giorno alle 10:00
# Rimuovere con:  Unregister-ScheduledTask -TaskName 'RiserveVeneto-*' -Confirm:$false
$dir = $PSScriptRoot
$py  = Join-Path $dir '.venv\Scripts\python.exe'
$log = Join-Path $dir 'dati\raccolta.log'

function Registra($nome, $argomenti, $trigger) {
    $cmd = "/c cd /d `"$dir`" && `"$py`" -m raccolta $argomenti >> `"$log`" 2>&1"
    $azione = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmd
    $opz = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $nome -Action $azione -Trigger $trigger -Settings $opz -Force | Out-Null
    Write-Host "Registrata: $nome"
}

$orario = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
Registra 'RiserveVeneto-Fiumi' 'fiumi' $orario

$giorno = New-ScheduledTaskTrigger -Daily -At 10:00
Registra 'RiserveVeneto-Giornaliero-Falde' 'falde' $giorno
Registra 'RiserveVeneto-Giornaliero-Invasi' 'invasi' $giorno
Registra 'RiserveVeneto-Giornaliero-Bollettini' 'bollettini' $giorno
