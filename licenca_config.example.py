# Copie este arquivo para licenca_config.py e preencha com seus dados.
# NÃO commite licenca_config.py (contém token secreto).

LICENCA_REMOTA_ATIVA = True

# Repositório GitHub PRIVADO onde ficam os arquivos de licença
GITHUB_OWNER = "seu-usuario-github"
GITHUB_REPO = "licencas-clientes"
GITHUB_BRANCH = "main"
GITHUB_TOKEN = "ghp_SEU_TOKEN_AQUI"

# Pasta dentro do repositório
PASTA_LICENCAS = "licencas"

# Verificação periódica (segundos). Recomendado: 3600 = 1 hora
INTERVALO_VERIFICACAO_SEG = 3600

# Sem internet: manter liberado por até N horas após última verificação OK
GRACE_OFFLINE_HORAS = 72

# Atualização do executável (.exe) por GitHub Release
# Se não preencher, o sistema tenta reutilizar GITHUB_OWNER/GITHUB_REPO/GITHUB_TOKEN acima.
UPDATE_GITHUB_OWNER = "seu-usuario-github"
UPDATE_GITHUB_REPO = "seu-repo-do-exe"
UPDATE_GITHUB_TOKEN = "ghp_SEU_TOKEN_AQUI"

# Use "latest" para baixar a release mais recente
UPDATE_RELEASE_TAG = "latest"

# Nome exato do .exe anexado na release. Se deixar vazio, pega o primeiro .exe encontrado.
UPDATE_ASSET_NAME = "SistemaAutomacaoNFe.exe"
