# Scarica i PDF liberi nelle cartelle dei vari step.
# Uso: tasto destro sul file -> "Esegui con PowerShell",
# oppure dalla cartella: powershell -ExecutionPolicy Bypass -File .\scarica_pdf.ps1
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$files = @(
  @("01_basi_qkd_sdn\GEANT_QKD_Concepts_and_Considerations_2024.pdf", "https://resources.geant.org/wp-content/uploads/2024/02/GN5-1_White-Paper_QKD-Concepts-and-Considerations.pdf"),
  @("01_basi_qkd_sdn\Kreutz_2015_SDN_Comprehensive_Survey.pdf", "https://www.cs.utsa.edu/~korkmaz/teaching/ds-resources/sharvari-papers/survey-2015-Kreutz-sdn-comp-survey.pdf"),
  @("02_qkd015_controller\ETSI_GS_QKD_015_V2.1.1.pdf", "https://www.etsi.org/deliver/etsi_gs/QKD/001_099/015/02.01.01_60/gs_QKD015v020101p.pdf"),
  @("03_qkd018_standard_e_yang\ETSI_GS_QKD_018_V1.1.1.pdf", "https://www.etsi.org/deliver/etsi_gs/QKD/001_099/018/01.01.01_60/gs_qkd018v010101p.pdf"),
  @("04_rfc8453_analogia_actn\RFC8453_ACTN.pdf", "https://www.rfc-editor.org/rfc/rfc8453.pdf"),
  @("05_casi_uso\1_Sim_2023_Entropy_SDN_Orchestration_QKD.pdf", "https://www.mdpi.com/1099-4300/25/6/943/pdf"),
  @("05_casi_uso\3_SebastianLombrana_2024_MadQCI_blueprint.pdf", "https://arxiv.org/pdf/2409.01069v1"),
  @("99_approfondimenti\ETSI_GS_QKD_004_V2.1.1.pdf", "https://www.etsi.org/deliver/etsi_gs/QKD/001_099/004/02.01.01_60/gs_QKD004v020101p.pdf"),
  @("99_approfondimenti\Cao_2022_COMST_Evolution_QKD_Networks.pdf", "https://eprints.soton.ac.uk/454194/1/COMST_00444_2021_final_manuscript.pdf"),
  @("99_approfondimenti\Aguado_2016_Secure_NFV_SDN_QKD.pdf", "https://arxiv.org/pdf/1604.05861"),
  @("99_approfondimenti\Mendez_2026_Switching_Coordinator.pdf", "https://arxiv.org/pdf/2603.13812"),
  @("99_approfondimenti\Laschet_2026_SDN_Key_Relay.pdf", "https://arxiv.org/pdf/2608.17539"),
  @("99_approfondimenti\QKD_Routing_Overview_2024.pdf", "https://arxiv.org/pdf/2407.13156")
)
foreach ($f in $files) {
  if (Test-Path $f[0]) { Write-Host "gia' presente: $($f[0])"; continue }
  try { Invoke-WebRequest -Uri $f[1] -OutFile $f[0] -UserAgent "Mozilla/5.0"; Write-Host "OK  $($f[0])" }
  catch { Write-Host "ERR $($f[0]) -> scaricalo a mano da $($f[1])" }
}
# Modelli YANG (richiede git)
if (Get-Command git -ErrorAction SilentlyContinue) {
  if (-not (Test-Path "02_qkd015_controller\yang_qkd015")) { git clone --depth 1 https://forge.etsi.org/rep/qkd/gs015-ctrl-int-sdn.git 02_qkd015_controller\yang_qkd015 }
  if (-not (Test-Path "03_qkd018_standard_e_yang\yang_qkd018")) { git clone --depth 1 https://forge.etsi.org/rep/qkd/gs018-orch-int-sdn.git 03_qkd018_standard_e_yang\yang_qkd018 }
}
Write-Host "Fatto."
