-- Schema PostgreSQL — Sistema Web NFe (autônomo)

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nome_completo TEXT,
    email TEXT UNIQUE,
    senha_hash BYTEA
);

CREATE TABLE IF NOT EXISTS configuracoes (
    id INTEGER PRIMARY KEY DEFAULT 1,
    link_sistema TEXT,
    usuario_sistema TEXT,
    senha_sistema_criptografada BYTEA,
    email_smtp TEXT,
    email_usuario TEXT,
    email_senha_criptografada BYTEA,
    email_ssl INTEGER DEFAULT 1,
    email_porta TEXT DEFAULT '',
    email_agendamento_tipo TEXT DEFAULT '',
    email_intervalo_horas INTEGER DEFAULT 1,
    email_proxima_execucao TEXT DEFAULT '',
    email_ultima_execucao TEXT DEFAULT '',
    email_destinatarios TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS suporte_envios_automaticos (
    chave TEXT PRIMARY KEY,
    horario TEXT,
    enviado_em TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tokens (
    id SERIAL PRIMARY KEY,
    token_hash TEXT UNIQUE,
    data_insercao DATE,
    ultima_checagem DATE,
    status TEXT DEFAULT 'PENDENTE'
);

CREATE TABLE IF NOT EXISTS notas_fiscais (
    id SERIAL PRIMARY KEY,
    numero_nf TEXT,
    status TEXT DEFAULT 'Pendente',
    data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filtros_salvos (
    id SERIAL PRIMARY KEY,
    mes TEXT,
    ano TEXT,
    cod_filial TEXT DEFAULT '',
    cod_unidade_embarque TEXT DEFAULT '',
    ultimos_30_dias INTEGER DEFAULT 0,
    hoje_apenas INTEGER DEFAULT 0,
    fornecedores_fatura_afaturar TEXT DEFAULT '',
    cod_tipo_fornecedor TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notas_raspadas (
    id SERIAL PRIMARY KEY,
    status TEXT,
    fornecedor TEXT,
    num_nota TEXT,
    data_em TEXT,
    valor TEXT,
    sit_nfe TEXT,
    chave_nfe TEXT,
    filial TEXT,
    user_ins TEXT,
    codigo_interno TEXT,
    erro_importacao TEXT,
    observacao_nfe TEXT,
    data_insercao TEXT DEFAULT '',
    nfe_estoque TEXT DEFAULT '☐',
    nfe_arquiva TEXT DEFAULT '☐',
    painel_placa TEXT DEFAULT '',
    painel_km TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS frota_erp (
    codveiculo INTEGER PRIMARY KEY,
    cavalo TEXT DEFAULT '',
    placa TEXT,
    carreta1 TEXT DEFAULT '',
    carreta2 TEXT DEFAULT '',
    carreta3 TEXT DEFAULT '',
    veiculoproprio TEXT DEFAULT '',
    ultima_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    movimentacao_carreta TEXT DEFAULT '',
    data_movimentacao TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS frota_historico_movimentacao (
    id SERIAL PRIMARY KEY,
    placa_carreta TEXT,
    cod_veiculo_origem INTEGER,
    placa_cavalo_origem TEXT,
    cod_veiculo_destino INTEGER,
    placa_cavalo_destino TEXT,
    data_movimentacao TEXT,
    texto_resumo TEXT
);

CREATE TABLE IF NOT EXISTS licenca_sistema (
    id INTEGER PRIMARY KEY DEFAULT 1,
    data_expiracao TEXT,
    token_usado TEXT
);

CREATE TABLE IF NOT EXISTS instalacao_licenca (
    id INTEGER PRIMARY KEY DEFAULT 1,
    instalacao_id TEXT,
    data_criacao TEXT,
    status TEXT,
    razao_social TEXT DEFAULT '',
    nome_arquivo_github TEXT DEFAULT '',
    ultimo_upload TEXT DEFAULT '',
    ultima_verificacao TEXT DEFAULT '',
    ultimo_ativado_github TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS config_placas (
    id SERIAL PRIMARY KEY,
    modelo TEXT
);

CREATE TABLE IF NOT EXISTS config_km (
    id SERIAL PRIMARY KEY,
    modelo TEXT
);

CREATE TABLE IF NOT EXISTS config_sistema (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS config_combustiveis (
    id INTEGER PRIMARY KEY DEFAULT 1,
    cod_etanol TEXT DEFAULT '',
    cod_gasolina TEXT DEFAULT '',
    cod_s10 TEXT DEFAULT '',
    cod_s500 TEXT DEFAULT '',
    cod_arla TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS itens_erp (
    coditemd TEXT PRIMARY KEY,
    descgrupoimp TEXT DEFAULT '',
    descnegocioimp TEXT DEFAULT '',
    descricao TEXT DEFAULT '',
    ultima_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS config_relatorios (
    id INTEGER PRIMARY KEY DEFAULT 1,
    rel_veiculo TEXT DEFAULT '',
    rel_item TEXT DEFAULT '',
    cod_grupo_item TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS logs_sessoes (
    sessao_id TEXT PRIMARY KEY,
    origem TEXT,
    descricao TEXT,
    iniciada_em TEXT,
    finalizada_em TEXT DEFAULT '',
    status TEXT DEFAULT 'EM_ANDAMENTO'
);

CREATE TABLE IF NOT EXISTS logs_execucao (
    id SERIAL PRIMARY KEY,
    sessao_id TEXT,
    origem TEXT,
    nivel TEXT DEFAULT 'INFO',
    mensagem TEXT,
    criado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_notas_raspadas_chave ON notas_raspadas(chave_nfe);
CREATE INDEX IF NOT EXISTS idx_notas_raspadas_num ON notas_raspadas(num_nota);
CREATE INDEX IF NOT EXISTS idx_logs_execucao_sessao ON logs_execucao(sessao_id);

-- Padrões iguais ao app Windows (sistema_automacao.db)
INSERT INTO config_placas (modelo)
SELECT v FROM (VALUES
    ('PLACA: AAA-1A11'),
    ('PLAC: AAA 1A11'),
    ('PLACA: AAA1A11'),
    ('PLAC: AAA1111')
) AS t(v)
WHERE NOT EXISTS (SELECT 1 FROM config_placas LIMIT 1);

INSERT INTO config_km (modelo)
SELECT v FROM (VALUES
    ('KM: 1'),
    ('KM 1'),
    ('ODOMETRO : 1'),
    ('ODOMETRO: 1'),
    ('HIDROMETRO: 1'),
    ('ODO: 1')
) AS t(v)
WHERE NOT EXISTS (SELECT 1 FROM config_km LIMIT 1);

INSERT INTO config_combustiveis (id, cod_etanol, cod_gasolina, cod_s10, cod_s500, cod_arla)
SELECT 1, '', '', '', '', ''
WHERE NOT EXISTS (SELECT 1 FROM config_combustiveis WHERE id = 1);

INSERT INTO config_relatorios (id, rel_veiculo, rel_item, cod_grupo_item)
SELECT 1, '117', '118', ''
WHERE NOT EXISTS (SELECT 1 FROM config_relatorios WHERE id = 1);
