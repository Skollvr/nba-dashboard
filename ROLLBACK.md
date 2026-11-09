# Como voltar à versão de 09/11/2026

O backup completo, criado **antes de qualquer alteração**, está em:

- Branch: `backup/pre-improvements-2026-11-09`
- Commit: `002af0a0656c939987e86dbf29dc336cb3b4ce26`

O backup contém o repositório inteiro, inclusive os dados de previsão e de
resultados armazenados no momento da cópia.

## Reversão segura do aplicativo sem perder novos resultados

**Não faça reset forçado de `main`**, porque o monitor acrescenta novos
dados desde o backup. Para recuperar o código antigo e preservar a pasta
`data/` mais recente:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c rollback/versao-20261109

# Restaura código e interface do backup, inclusive remove arquivos novos,
# mas mantém todas as previsões/resultados coletados depois do backup.
git restore --source=origin/backup/pre-improvements-2026-11-09 \
  --staged --worktree -- . ':!data'

git diff --cached --stat
git commit -m "restore: revert application code to 2026-11-09 backup"
git push -u origin rollback/versao-20261109
```

Abra um Pull Request de `rollback/versao-20261109` para `main` e confirme
os testes. Assim o retorno cria um commit reversível e não apaga registros
novos. Caso queira reverter **também** os dados, faça primeiro outro backup
dos arquivos atuais, pois isso descartaria avaliações posteriores.

Em vez dos comandos locais, a mesma reversão pode ser executada diretamente
pelo GitHub, criando uma branch de rollback e um PR.
