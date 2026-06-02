from __future__ import annotations

import io
import html as html_lib
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urldefrag, urljoin

import requests
import streamlit as st


BASE_URL = "http://pesquisa.doe.seplag.ce.gov.br/doepesquisa/"
ULTIMAS_URL = urljoin(
    BASE_URL,
    "sead.do?page=ultimasEdicoes&cmd=11&action=Ultimas",
)
DETALHE_URL = urljoin(
    BASE_URL,
    "sead.do?page=ultimasDetalhe&cmd=10&action=Cadernos&data={data}",
)
PESQUISA_TEXTUAL_INICIAL_URL = urljoin(
    BASE_URL,
    "sead.to?page=pesquisaTextual&cmd=11&action=InicialTextual&flag=0",
)
PESQUISA_TEXTUAL_URL = urljoin(BASE_URL, "sead.to")
NAVEGAR_PESQUISA_URL = urljoin(
    BASE_URL,
    "sead.do?page=pesquisaTextual&cmd={cmd}&action=NavegarBasico&flag=1",
)
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "diario_oficial_ceara"
REQUEST_TIMEOUT = 45
PUBLIC_MODE = os.getenv("DOE_PUBLIC_MODE", "").strip().lower() in {"1", "true", "sim", "yes"}


@dataclass(frozen=True)
class DiarioArquivo:
    nome: str
    url: str
    conteudo: bytes | None = None


@dataclass(frozen=True)
class PesquisaResultado:
    data_diario: str
    numero_diario: str
    caderno: str
    pagina: str
    url_pdf: str


@dataclass(frozen=True)
class PesquisaPaginacao:
    total_registros: int
    pagina_atual: int
    total_paginas: int


class ResultadoPesquisaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.linhas: list[list[tuple[str, str | None]]] = []
        self._linha_atual: list[tuple[str, str | None]] | None = None
        self._texto_celula: list[str] | None = None
        self._link_celula: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        if tag.lower() == "tr":
            self._linha_atual = []
        elif tag.lower() == "td" and self._linha_atual is not None:
            self._texto_celula = []
            self._link_celula = None
        elif tag.lower() == "a" and self._texto_celula is not None:
            href = atributos.get("href")
            if href and ".pdf" in href.lower():
                self._link_celula = urljoin(BASE_URL, href)

    def handle_data(self, data: str) -> None:
        if self._texto_celula is not None:
            self._texto_celula.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td" and self._linha_atual is not None and self._texto_celula is not None:
            self._linha_atual.append((limpar_texto(" ".join(self._texto_celula)), self._link_celula))
            self._texto_celula = None
            self._link_celula = None
        elif tag.lower() == "tr" and self._linha_atual is not None:
            if self._linha_atual:
                self.linhas.append(self._linha_atual)
            self._linha_atual = None


def data_para_chave(data_publicacao: date) -> str:
    return data_publicacao.strftime("%Y%m%d")


def data_para_br(data_publicacao: date) -> str:
    return data_publicacao.strftime("%d/%m/%Y")


def criar_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        }
    )
    return sessao


def obter_html(sessao: requests.Session, url: str, params: dict[str, str] | None = None) -> str:
    resposta = sessao.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    resposta.encoding = resposta.apparent_encoding or "iso-8859-1"
    return resposta.text


def limpar_texto(valor: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(valor)).strip()


def remover_tags(html_text: str) -> str:
    return limpar_texto(re.sub(r"<[^>]+>", " ", html_text))


def obter_ultima_data_publicada(sessao: requests.Session) -> date | None:
    html_text = obter_html(sessao, ULTIMAS_URL)
    texto = remover_tags(html_text)
    match = re.search(r"ltimo\s+Di.rio\s+publicado\s*\(\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%d/%m/%Y").date()


def listar_arquivos_do_dia(sessao: requests.Session, data_publicacao: date) -> list[DiarioArquivo]:
    chave_data = data_para_chave(data_publicacao)
    html_text = obter_html(sessao, DETALHE_URL.format(data=chave_data))
    padrao_pdf = re.compile(
        r"href=['\"](?P<url>[^'\"]+\.pdf)['\"][^>]*>\s*(?:Visualizar|Baixar)?",
        re.I,
    )

    arquivos: list[DiarioArquivo] = []
    vistos: set[str] = set()

    for match in padrao_pdf.finditer(html_text):
        url = match.group("url")
        url_absoluta = urljoin(BASE_URL, url)
        nome = Path(url_absoluta.split("?")[0]).name
        if url_absoluta not in vistos:
            arquivos.append(DiarioArquivo(nome=nome, url=url_absoluta))
            vistos.add(url_absoluta)

    return arquivos


def baixar_arquivo(sessao: requests.Session, arquivo: DiarioArquivo) -> DiarioArquivo:
    resposta = sessao.get(arquivo.url, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()

    tipo = resposta.headers.get("content-type", "").lower()
    if "pdf" not in tipo and not resposta.content.startswith(b"%PDF"):
        raise ValueError(f"O arquivo {arquivo.nome} nao parece ser um PDF valido.")

    return DiarioArquivo(nome=arquivo.nome, url=arquivo.url, conteudo=resposta.content)


def salvar_arquivos(arquivos: Iterable[DiarioArquivo], pasta_destino: Path) -> list[Path]:
    pasta_destino.mkdir(parents=True, exist_ok=True)
    caminhos: list[Path] = []
    for arquivo in arquivos:
        if arquivo.conteudo is None:
            continue
        caminho = pasta_destino / arquivo.nome
        caminho.write_bytes(arquivo.conteudo)
        caminhos.append(caminho)
    return caminhos


def montar_zip(arquivos: Iterable[DiarioArquivo]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as pacote:
        for arquivo in arquivos:
            if arquivo.conteudo is not None:
                pacote.writestr(arquivo.nome, arquivo.conteudo)
    return buffer.getvalue()


def extrair_resultados_pesquisa(html_text: str) -> list[PesquisaResultado]:
    parser = ResultadoPesquisaParser()
    parser.feed(html_text)

    resultados: list[PesquisaResultado] = []
    for linha in parser.linhas:
        if len(linha) < 4:
            continue

        data_diario, link_pdf = linha[0]
        if not link_pdf or not re.search(r"\d{2}[-/]\d{2}[-/]\d{4}", data_diario):
            continue

        resultados.append(
            PesquisaResultado(
                data_diario=data_diario,
                numero_diario=linha[1][0],
                caderno=linha[2][0],
                pagina=linha[3][0],
                url_pdf=link_pdf,
            )
        )

    return resultados


def extrair_paginacao_pesquisa(html_text: str) -> PesquisaPaginacao | None:
    texto = remover_tags(html_text)
    match = re.search(
        r"(\d+)\s+registros\s+encontrados\.\s+Pagina\s+(\d+)\s+de\s+(\d+)",
        texto,
        re.I,
    )
    if not match:
        return None

    return PesquisaPaginacao(
        total_registros=int(match.group(1)),
        pagina_atual=int(match.group(2)),
        total_paginas=int(match.group(3)),
    )


def validar_pesquisa_textual(termo: str, data_inicio: date, data_fim: date) -> None:
    if not termo.strip():
        raise ValueError("Informe o texto que deseja pesquisar.")
    if data_fim < data_inicio:
        raise ValueError("A data final nao pode ser menor que a data inicial.")
    if (data_fim - data_inicio).days > 366:
        raise ValueError("O site do DOE limita a pesquisa textual a intervalos de ate 1 ano.")

    palavras = termo.split()
    if len(palavras) == 1 and (len(palavras[0]) < 6 or palavras[0].lower() == "secretaria"):
        raise ValueError("Para uma unica palavra, use uma palavra com 6 letras ou mais e evite 'secretaria'.")


def pesquisar_textual(
    sessao: requests.Session,
    termo: str,
    modo: str,
    data_inicio: date,
    data_fim: date,
    num_diario: str = "",
    num_caderno: str = "",
    num_pagina: str = "",
    max_paginas: int = 1,
) -> tuple[list[PesquisaResultado], PesquisaPaginacao | None, int]:
    validar_pesquisa_textual(termo, data_inicio, data_fim)

    radio = "radio1" if modo == "Que tenha todas as palavras" else "radio3"
    params = {
        "page": "pesquisaTextual",
        "action": "PesquisarTextual",
        "cmd": "11",
        "flag": "1",
        "dataini": data_para_br(data_inicio),
        "datafim": data_para_br(data_fim),
        "numDiario": num_diario.strip(),
        "numCaderno": num_caderno.strip(),
        "numPagina": num_pagina.strip(),
        "RadioGroup1": radio,
        "pesqAnd": termo.strip() if radio == "radio1" else "",
        "pesqEx": termo.strip() if radio == "radio3" else "",
        "consultar": "",
    }

    obter_html(sessao, PESQUISA_TEXTUAL_INICIAL_URL)
    html_text = obter_html(sessao, PESQUISA_TEXTUAL_URL, params=params)
    resultados = extrair_resultados_pesquisa(html_text)
    paginacao = extrair_paginacao_pesquisa(html_text)
    paginas_lidas = 1

    while (
        paginacao is not None
        and paginacao.pagina_atual < paginacao.total_paginas
        and paginas_lidas < max_paginas
    ):
        html_text = obter_html(sessao, NAVEGAR_PESQUISA_URL.format(cmd="proximo"))
        resultados.extend(extrair_resultados_pesquisa(html_text))
        paginacao = extrair_paginacao_pesquisa(html_text) or paginacao
        paginas_lidas += 1

    vistos: set[tuple[str, str, str, str]] = set()
    unicos: list[PesquisaResultado] = []
    for resultado in resultados:
        chave = (resultado.url_pdf, resultado.data_diario, resultado.caderno, resultado.pagina)
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(resultado)

    return unicos, paginacao, paginas_lidas


def arquivos_de_resultados(resultados: Iterable[PesquisaResultado]) -> list[DiarioArquivo]:
    arquivos: list[DiarioArquivo] = []
    vistos: set[str] = set()

    for resultado in resultados:
        url_sem_fragmento = urldefrag(resultado.url_pdf).url
        if url_sem_fragmento in vistos:
            continue
        vistos.add(url_sem_fragmento)
        arquivos.append(DiarioArquivo(nome=Path(url_sem_fragmento).name, url=url_sem_fragmento))

    return arquivos


def executar_download(data_publicacao: date, pasta_destino: Path) -> tuple[list[DiarioArquivo], list[Path]]:
    sessao = criar_sessao()
    encontrados = listar_arquivos_do_dia(sessao, data_publicacao)
    baixados = [baixar_arquivo(sessao, arquivo) for arquivo in encontrados]
    caminhos = salvar_arquivos(baixados, pasta_destino)
    return baixados, caminhos


st.set_page_config(
    page_title="DOE Ceara - Baixar publicacoes",
    page_icon="CE",
    layout="wide",
)

st.title("Diario Oficial do Ceara")
st.caption("Automacao em Python para baixar edicoes e pesquisar publicacoes no DOE.")

with st.sidebar:
    st.header("Configuracao geral")
    if PUBLIC_MODE:
        st.info("Modo publico ativo: os PDFs sao entregues em ZIP pelo navegador.")
        pasta_destino = DEFAULT_DOWNLOAD_DIR
    else:
        pasta_texto = st.text_input("Pasta para salvar PDFs", value=str(DEFAULT_DOWNLOAD_DIR))
        pasta_destino = Path(pasta_texto).expanduser()
    st.link_button("Abrir site do DOE", ULTIMAS_URL, use_container_width=True)

aba_download, aba_pesquisa = st.tabs(["Baixar publicacoes do dia", "Pesquisa textual"])

with aba_download:
    st.subheader("Baixar publicacoes do dia")

    modo_data = st.radio(
        "Data para buscar",
        ["Hoje", "Ultima publicada no site", "Escolher data"],
        index=0,
        horizontal=True,
    )

    data_escolhida = st.date_input(
        "Data",
        value=date.today(),
        format="DD/MM/YYYY",
        disabled=modo_data != "Escolher data",
        key="download_data",
    )

    baixar = st.button("Baixar publicacoes", type="primary", key="baixar_publicacoes")

    st.info("Se nao houver publicacao na data escolhida, a lista ficara vazia.")

    if baixar:
        try:
            sessao = criar_sessao()

            if modo_data == "Ultima publicada no site":
                with st.spinner("Consultando a ultima publicacao informada pelo site..."):
                    data_publicacao = obter_ultima_data_publicada(sessao)
                if data_publicacao is None:
                    st.error("Nao consegui identificar a ultima data publicada na pagina inicial.")
                    st.stop()
            elif modo_data == "Escolher data":
                data_publicacao = data_escolhida
            else:
                data_publicacao = date.today()

            with st.spinner(f"Buscando arquivos de {data_para_br(data_publicacao)}..."):
                arquivos = listar_arquivos_do_dia(sessao, data_publicacao)

            if not arquivos:
                st.warning(f"Nenhum PDF encontrado para {data_para_br(data_publicacao)}.")
                st.stop()

            progresso = st.progress(0)
            baixados: list[DiarioArquivo] = []
            for indice, arquivo in enumerate(arquivos, start=1):
                with st.spinner(f"Baixando {arquivo.nome}..."):
                    baixados.append(baixar_arquivo(sessao, arquivo))
                progresso.progress(indice / len(arquivos))

            pacote_zip = montar_zip(baixados)
            nome_zip = f"diario_oficial_ceara_{data_para_chave(data_publicacao)}.zip"

            if PUBLIC_MODE:
                caminhos = []
                st.success(f"{len(baixados)} arquivo(s) pronto(s) para baixar.")
            else:
                caminhos = salvar_arquivos(baixados, pasta_destino)
                st.success(f"{len(caminhos)} arquivo(s) salvo(s) em: {pasta_destino}")

            st.download_button(
                "Baixar tudo em ZIP",
                data=pacote_zip,
                file_name=nome_zip,
                mime="application/zip",
                use_container_width=True,
            )

            itens_exibicao = zip(caminhos or [None] * len(baixados), baixados)
            for caminho, arquivo in itens_exibicao:
                tamanho_kb = len(arquivo.conteudo or b"") / 1024
                nome = caminho.name if caminho is not None else arquivo.nome
                st.write(f"- {nome} ({tamanho_kb:,.1f} KB)")
                st.caption(arquivo.url)

        except requests.HTTPError as erro:
            st.error(f"O site respondeu com erro: {erro}")
        except requests.RequestException as erro:
            st.error(f"Nao foi possivel acessar o site do DOE: {erro}")
        except Exception as erro:
            st.error(f"Falha durante a automacao: {erro}")
    else:
        st.write("Clique em **Baixar publicacoes** para iniciar.")

with aba_pesquisa:
    st.subheader("Pesquisa textual automatica")
    st.write("Use os mesmos filtros da pesquisa textual oficial do DOE.")

    with st.form("form_pesquisa_textual"):
        col_datas_1, col_datas_2 = st.columns(2)
        data_inicio = col_datas_1.date_input(
            "Data inicial",
            value=date.today(),
            format="DD/MM/YYYY",
            key="pesquisa_data_inicio",
        )
        data_fim = col_datas_2.date_input(
            "Data final",
            value=date.today(),
            format="DD/MM/YYYY",
            key="pesquisa_data_fim",
        )

        modo_pesquisa = st.radio(
            "Tipo de consulta",
            ["Que tenha todas as palavras", "Exatamente a frase"],
            horizontal=True,
        )
        termo = st.text_input("Texto da pesquisa", placeholder="Ex.: governador")

        with st.expander("Filtros opcionais"):
            col_filtro_1, col_filtro_2, col_filtro_3, col_filtro_4 = st.columns(4)
            num_diario = col_filtro_1.text_input("N do Diario", placeholder="Ex.: 98")
            num_caderno = col_filtro_2.text_input("N do Caderno", placeholder="Ex.: 1")
            num_pagina = col_filtro_3.text_input("N da Pagina", placeholder="Ex.: 11")
            max_paginas = col_filtro_4.number_input(
                "Paginas do site",
                min_value=1,
                max_value=10,
                value=1,
                step=1,
            )

        pesquisar = st.form_submit_button("Pesquisar no DOE", type="primary")

    if pesquisar:
        try:
            with st.spinner("Consultando a pesquisa textual do DOE..."):
                sessao = criar_sessao()
                resultados, paginacao, paginas_lidas = pesquisar_textual(
                    sessao=sessao,
                    termo=termo,
                    modo=modo_pesquisa,
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    num_diario=num_diario,
                    num_caderno=num_caderno,
                    num_pagina=num_pagina,
                    max_paginas=int(max_paginas),
                )

            st.session_state["pesquisa_textual_resultados"] = resultados
            st.session_state["pesquisa_textual_paginacao"] = paginacao
            st.session_state["pesquisa_textual_paginas_lidas"] = paginas_lidas
            st.session_state["pesquisa_textual_termo"] = termo

        except requests.HTTPError as erro:
            st.error(f"O site respondeu com erro: {erro}")
        except requests.RequestException as erro:
            st.error(f"Nao foi possivel acessar a pesquisa textual do DOE: {erro}")
        except Exception as erro:
            st.error(f"Falha durante a pesquisa textual: {erro}")

    resultados_salvos: list[PesquisaResultado] = st.session_state.get("pesquisa_textual_resultados", [])
    paginacao_salva: PesquisaPaginacao | None = st.session_state.get("pesquisa_textual_paginacao")
    paginas_lidas_salvas = st.session_state.get("pesquisa_textual_paginas_lidas", 0)

    if resultados_salvos:
        if paginacao_salva is not None:
            st.success(
                f"{len(resultados_salvos)} resultado(s) carregado(s). "
                f"O site informou {paginacao_salva.total_registros} registro(s) em "
                f"{paginacao_salva.total_paginas} pagina(s); foram lida(s) {paginas_lidas_salvas}."
            )
        else:
            st.success(f"{len(resultados_salvos)} resultado(s) encontrado(s).")

        dados = [
            {
                "Data do Diario": resultado.data_diario,
                "Diario": resultado.numero_diario,
                "Caderno": resultado.caderno,
                "Pagina": resultado.pagina,
                "Abrir PDF": resultado.url_pdf,
            }
            for resultado in resultados_salvos
        ]
        st.dataframe(
            dados,
            hide_index=True,
            use_container_width=True,
            column_config={"Abrir PDF": st.column_config.LinkColumn("Abrir PDF")},
        )

        if st.button("Baixar PDFs encontrados", key="baixar_pesquisa"):
            try:
                sessao = criar_sessao()
                arquivos_pdf = arquivos_de_resultados(resultados_salvos)
                progresso = st.progress(0)
                baixados: list[DiarioArquivo] = []
                for indice, arquivo in enumerate(arquivos_pdf, start=1):
                    with st.spinner(f"Baixando {arquivo.nome}..."):
                        baixados.append(baixar_arquivo(sessao, arquivo))
                    progresso.progress(indice / len(arquivos_pdf))

                pacote_zip = montar_zip(baixados)

                if PUBLIC_MODE:
                    st.success(f"{len(baixados)} PDF(s) pronto(s) para baixar.")
                else:
                    caminhos = salvar_arquivos(baixados, pasta_destino)
                    st.success(f"{len(caminhos)} PDF(s) salvo(s) em: {pasta_destino}")

                st.download_button(
                    "Baixar ZIP da pesquisa",
                    data=pacote_zip,
                    file_name="pesquisa_textual_doe_ceara.zip",
                    mime="application/zip",
                    use_container_width=True,
                )
            except Exception as erro:
                st.error(f"Falha ao baixar PDFs da pesquisa: {erro}")
    elif pesquisar:
        st.warning("Nenhum resultado encontrado para os filtros informados.")
    else:
        st.write("Preencha a pesquisa e clique em **Pesquisar no DOE**.")
