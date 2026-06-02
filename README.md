# Aplicativo Streamlit - DOE Ceara

Automacao em Python para baixar publicacoes do dia e fazer pesquisa textual no Diario Oficial do Estado do Ceara.

## Como executar

1. Instale as dependencias:

```powershell
pip install -r requirements.txt
```

2. Abra o aplicativo:

```powershell
streamlit run app.py
```

3. Na tela, use uma das abas:

### Baixar publicacoes do dia

- `Hoje`: tenta baixar os PDFs da data atual.
- `Ultima publicada no site`: usa a data que o proprio site informa como ultima publicacao.
- `Escolher data`: permite testar uma data especifica.

### Pesquisa textual

Essa aba automatiza a pesquisa textual oficial do site do DOE. Ela permite informar:

- data inicial e data final;
- texto pesquisado;
- busca por todas as palavras ou exatamente a frase;
- filtros opcionais de numero do diario, caderno e pagina;
- quantidade de paginas de resultado do site que devem ser lidas.

Os resultados aparecem em tabela com link direto para o PDF. Tambem e possivel baixar os PDFs encontrados em uma pasta local e gerar um ZIP.

Os PDFs sao salvos por padrao em:

```text
Downloads\diario_oficial_ceara
```
