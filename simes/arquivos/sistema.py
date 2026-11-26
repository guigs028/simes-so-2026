# -*- coding: utf-8 -*-
"""
Sistema de arquivos simulado (camada 3).

Referência: Silberschatz, 9ª ed., cap. 11 — operações, FCB, tabelas de arquivos abertos,
métodos de acesso, estrutura de diretórios, links e proteção.

O que já vem pronto (FORNECIDO): a criação da raiz, as tabelas e os métodos auxiliares
(nome começando com _). Implementem os métodos marcados com TODO.

=====================================================================================
REGRAS DE CUSTO (iguais para todos os grupos — sigam à risca)
=====================================================================================
Um acesso = um bloco lido ou gravado (use _ler, _escrever e _aplicar; eles contam tudo).

Diretórios: cada diretório ocupa 1 bloco (o bloco 0 do diretório, obtido com
_bloco_do_diretorio). Ler um diretório = 1 leitura desse bloco.

resolver("/a/b/c")   lê os diretórios "/", "/a" e "/a/b" (3 leituras) e devolve o FCB de c.
                     resolver("/") devolve a raiz sem ler nada.
Operações que criam, removem ou alteram uma entrada leem os diretórios do caminho até o
pai (inclusive), exatamente como resolver, e depois:

mkdir(p)             + aloca 1 bloco para o novo diretório, grava o bloco do novo diretório
                       (1 escrita) e o bloco do pai (1 escrita)
create(p, tamanho)   + aloca ceil(tamanho / TAMANHO_BLOCO) blocos (conteúdo não é gravado)
                       e grava o bloco do pai (1 escrita)
open(p, modo, h)     lê os diretórios como resolver; nenhuma escrita
read(h, n)           lê os blocos que contêm os bytes [posição, posição + n), limitados ao
                     tamanho do arquivo; avança a posição; devolve os bytes lidos
write(h, n)          se precisar, estende o arquivo (alocador.estender); grava os blocos que
                     contêm [posição, posição + n); avança a posição; atualiza o tamanho e
                     marca o FCB como sujo. No modo 'a', a escrita começa no fim do arquivo.
seek(h, posicao)     muda a posição; nenhum acesso
close(h)             se o FCB está sujo, grava o bloco do diretório pai (1 escrita) e limpa
                     o "sujo"; atualiza as tabelas
unlink(p)            + grava o bloco do pai (1 escrita). Se o arquivo ficar sem links e
                     ninguém o tiver aberto, libera os blocos; se estiver aberto, a liberação
                     acontece no último close.

Tabelas de arquivos abertos:
- self.tabela_global: dict fcb_id -> EntradaGlobal. Uma entrada por arquivo aberto; o
  contador soma as aberturas de todos os processos. A entrada sai quando o contador zera.
- self.tabelas_processo: dict processo -> {handle: EntradaProcesso}. Uma entrada por
  abertura, com a posição e o modo.

Modos de abertura: 'r' (só leitura), 'w' (só escrita), 'rw' (ambos), 'a' (acréscimo, só
escrita). Ler com um handle sem 'r', ou escrever sem 'w'/'a': ErroSA('EBADF').
Erros: lancem ErroSA com o código adequado (veja estruturas.py).

Ganchos dos módulos (já chamados pelas operações do núcleo, que vocês implementam):
- _verificar_permissao(processo, fcb, modo): no núcleo não faz nada; o M5 implementa.
- _verificar_trava(processo, fcb):            no núcleo não faz nada; o M5 implementa.
  O núcleo deve chamar _verificar_permissao no open e _verificar_trava no read e no write.
"""
import math
from collections import Counter

from simes import parametros as P
from simes.arquivos.estruturas import FCB, EntradaGlobal, EntradaProcesso, ErroSA

MAX_LINKS_SIMBOLICOS = 8


class SistemaArquivos:

    def __init__(self, camada, alocador, bloqueio="consultivo"):
        self.camada = camada
        self.alocador = alocador
        self.bloqueio = bloqueio            # 'consultivo' ou 'mandatorio' (M5)
        self.fcbs = {}
        self._proximo_id = 0
        self.agora_ms = 0.0                 # atualizado pelo simulador antes de cada operação
        self.tabela_global = {}             # fcb_id -> EntradaGlobal
        self.tabelas_processo = {}          # processo -> {handle: EntradaProcesso}
        self.usuarios = {}                  # processo -> nome do usuário (M5); padrão: 'root'
        self.contadores = Counter()         # indicadores extras dos módulos (vão para o CSV)
        self.raiz = self._novo_fcb("/", "diretorio", None)
        self.alocador.alocar(self.raiz, 1)  # a raiz já existe ao "formatar": sem custo

    # ================================================================ FORNECIDO
    def _novo_fcb(self, nome, tipo, pai, processo=None):
        """Cria um FCB (dono = usuário do processo) e o registra em self.fcbs. Não aloca blocos."""
        fcb = FCB(self._proximo_id, nome, tipo, pai, self.agora_ms, self.usuario_de(processo))
        self.fcbs[fcb.id] = fcb
        self._proximo_id += 1
        return fcb

    def usuario_de(self, processo):
        return self.usuarios.get(processo, "root")

    def _ler(self, blocos):
        self.camada.ler(blocos)

    def _escrever(self, blocos):
        self.camada.escrever(blocos)

    def _aplicar(self, extras):
        """Executa a lista de acessos extras devolvida pelo alocador."""
        for tipo, bloco in extras:
            if tipo == "L":
                self._ler([bloco])
            else:
                self._escrever([bloco])

    def _bloco_do_diretorio(self, diretorio):
        """Bloco físico onde está o conteúdo do diretório (já executa os extras do alocador)."""
        bloco, extras = self.alocador.bloco_fisico(diretorio, 0)
        self._aplicar(extras)
        return bloco

    def _ler_diretorio(self, diretorio):
        self._ler([self._bloco_do_diretorio(diretorio)])

    def _gravar_diretorio(self, diretorio):
        self._escrever([self._bloco_do_diretorio(diretorio)])

    def _blocos_do_intervalo(self, fcb, inicio, fim):
        """Blocos físicos que contêm os bytes [inicio, fim) do arquivo (executa os extras)."""
        if fim <= inicio:
            return []
        primeiro = inicio // P.TAMANHO_BLOCO
        ultimo = (fim - 1) // P.TAMANHO_BLOCO
        blocos = []
        for i in range(primeiro, ultimo + 1):
            bloco, extras = self.alocador.bloco_fisico(fcb, i)
            self._aplicar(extras)
            blocos.append(bloco)
        return blocos

    @staticmethod
    def _blocos_necessarios(tamanho):
        return int(math.ceil(tamanho / float(P.TAMANHO_BLOCO)))

    @staticmethod
    def _dividir(caminho):
        if not caminho.startswith("/"):
            raise ErroSA("EINVAL", "o caminho deve ser absoluto: %s" % caminho)
        return [p for p in caminho.split("/") if p]

    def _entrada(self, processo, handle):
        try:
            return self.tabelas_processo[processo][handle]
        except KeyError:
            raise ErroSA("EBADF", "%s não tem o handle %s" % (processo, handle))

    def _liberar_arquivo(self, fcb):
        self.alocador.liberar(fcb)
        del self.fcbs[fcb.id]

    def _verificar_permissao(self, processo, fcb, modo):
        """Gancho do M5. No núcleo, tudo é permitido."""
        return None

    def _verificar_trava(self, processo, fcb):
        """Gancho do M5. No núcleo, travas não existem."""
        return None

    # ============================================================ núcleo (N3)
    MODOS = ("r", "w", "rw", "a")

    def resolver(self, caminho, seguir_simbolico=True):
        """Devolve o FCB do caminho absoluto, lendo os diretórios do caminho (ver regras).
        Erros: ENOENT, ENOTDIR. Com o M4: segue links simbólicos (inclusive no meio do
        caminho; o último componente só se seguir_simbolico=True); ELOOP depois de
        MAX_LINKS_SIMBOLICOS links."""
        partes = self._dividir(caminho)
        atual = self.raiz
        saltos = 0
        fim_alvo = 0                 # partes[:fim_alvo] vieram do alvo do último link seguido
        i = 0
        while i < len(partes):
            if atual.tipo != "diretorio":
                raise ErroSA("ENOTDIR", "/".join(partes[:i]))
            self._ler_diretorio(atual)
            fid = atual.entradas.get(partes[i])
            if fid is None:
                erro = ErroSA("ENOENT", caminho)
                erro.link_pendente = i < fim_alvo        # o alvo de um link simbólico não existe
                raise erro
            fcb = self.fcbs[fid]
            ultimo = i == len(partes) - 1
            if fcb.tipo == "simbolico" and (seguir_simbolico or not ultimo):
                saltos += 1
                if saltos > MAX_LINKS_SIMBOLICOS:
                    raise ErroSA("ELOOP", caminho)
                alvo = self._dividir(fcb.alvo)
                partes = alvo + partes[i + 1:]
                fim_alvo = len(alvo)
                atual, i = self.raiz, 0          # alvo absoluto: recomeça da raiz
                continue
            atual = fcb
            i += 1
        return atual

    def _resolver_pai(self, caminho):
        """Lê os diretórios até o pai (inclusive) e devolve (pai, nome)."""
        partes = self._dividir(caminho)
        if not partes:
            raise ErroSA("EEXIST", "/")
        pai = self.resolver("/" + "/".join(partes[:-1]))
        if pai.tipo != "diretorio":
            raise ErroSA("ENOTDIR", caminho)
        self._ler_diretorio(pai)
        return pai, partes[-1]

    def _nova_entrada(self, processo, caminho, tipo, n_blocos=0):
        """Comum a mkdir, create e symlink: confere o pai, cria o FCB e aloca os blocos."""
        pai, nome = self._resolver_pai(caminho)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", caminho)
        fcb = self._novo_fcb(nome, tipo, pai.id, processo)
        if tipo != "simbolico":
            try:
                self._aplicar(self.alocador.alocar(fcb, n_blocos))
            except ErroSA:
                del self.fcbs[fcb.id]
                raise
        pai.entradas[nome] = fcb.id
        pai.modificado_ms = self.agora_ms
        return pai, fcb

    def mkdir(self, processo, caminho):
        pai, fcb = self._nova_entrada(processo, caminho, "diretorio", 1)
        self._gravar_diretorio(fcb)
        self._gravar_diretorio(pai)

    def create(self, processo, caminho, tamanho):
        if tamanho < 0:
            raise ErroSA("EINVAL", "tamanho negativo")
        pai, fcb = self._nova_entrada(processo, caminho, "arquivo", self._blocos_necessarios(tamanho))
        fcb.tamanho = tamanho
        self._gravar_diretorio(pai)

    def open(self, processo, caminho, modo, handle):
        if modo not in self.MODOS:
            raise ErroSA("EINVAL", "modo %s" % modo)
        if handle in self.tabelas_processo.get(processo, {}):
            raise ErroSA("EINVAL", "%s já usa o handle %s" % (processo, handle))
        try:
            fcb = self.resolver(caminho)
        except ErroSA as e:
            if getattr(e, "link_pendente", False):
                self.contadores["links_pendentes"] += 1
            raise
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        self._verificar_permissao(processo, fcb, modo)
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None:
            entrada = self.tabela_global[fcb.id] = EntradaGlobal(fcb.id)
        entrada.contador += 1
        self.tabelas_processo.setdefault(processo, {})[handle] = EntradaProcesso(fcb.id, modo)

    def read(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if "r" not in e.modo:
            raise ErroSA("EBADF", "%s não foi aberto para leitura" % handle)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        fim = min(e.posicao + n, fcb.tamanho)
        if fim <= e.posicao:
            return 0
        self._ler(self._blocos_do_intervalo(fcb, e.posicao, fim))
        lidos, e.posicao = fim - e.posicao, fim
        return lidos

    def write(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if "w" not in e.modo and e.modo != "a":
            raise ErroSA("EBADF", "%s não foi aberto para escrita" % handle)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        if e.modo == "a":
            e.posicao = fcb.tamanho
        fim = e.posicao + n
        faltam = self._blocos_necessarios(fim) - len(fcb.blocos)
        if faltam > 0:
            self._aplicar(self.alocador.estender(fcb, faltam))
        self._escrever(self._blocos_do_intervalo(fcb, e.posicao, fim))
        e.posicao = fim
        fcb.tamanho = max(fcb.tamanho, fim)
        fcb.modificado_ms = self.agora_ms
        fcb.sujo = True
        return n

    def seek(self, processo, handle, posicao):
        e = self._entrada(processo, handle)
        if posicao < 0:
            raise ErroSA("EINVAL", "posição negativa")
        e.posicao = posicao

    def close(self, processo, handle):
        e = self._entrada(processo, handle)
        fcb = self.fcbs[e.fcb_id]
        if fcb.sujo:
            pai = self.fcbs.get(fcb.pai)
            if pai is not None:
                self._gravar_diretorio(pai)
            fcb.sujo = False
        del self.tabelas_processo[processo][handle]
        if not self.tabelas_processo[processo]:
            del self.tabelas_processo[processo]
        g = self.tabela_global[fcb.id]
        g.contador -= 1
        if g.trava == processo:
            g.trava = None
        if g.contador == 0:
            del self.tabela_global[fcb.id]
            if fcb.links == 0:                  # unlink com o arquivo aberto: libera agora
                self._liberar_arquivo(fcb)

    def unlink(self, processo, caminho):
        pai, nome = self._resolver_pai(caminho)
        fid = pai.entradas.get(nome)
        if fid is None:
            raise ErroSA("ENOENT", caminho)
        fcb = self.fcbs[fid]
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        del pai.entradas[nome]
        pai.modificado_ms = self.agora_ms
        fcb.links -= 1
        self._gravar_diretorio(pai)
        if fcb.links == 0 and fcb.id not in self.tabela_global:
            self._liberar_arquivo(fcb)

    # ============================================================ módulo M4
    def link(self, processo, existente, novo):
        """Hard link: lê os diretórios até `existente` (como resolver) e até o pai de `novo`;
        cria a entrada; links += 1; grava o bloco do pai de `novo` (1 escrita).
        Hard link para diretório: ErroSA('EPERM')."""
        fcb = self.resolver(existente, seguir_simbolico=False)
        if fcb.tipo == "diretorio":
            raise ErroSA("EPERM", "hard link para diretório: %s" % existente)
        pai, nome = self._resolver_pai(novo)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", novo)
        pai.entradas[nome] = fcb.id
        fcb.links += 1
        self._gravar_diretorio(pai)

    def symlink(self, processo, alvo, caminho):
        """Link simbólico: lê os diretórios até o pai de `caminho`; cria um FCB do tipo
        'simbolico' com fcb.alvo = alvo (sem blocos de dados); grava o bloco do pai (1 escrita).
        O alvo não precisa existir."""
        self._dividir(alvo)                     # o alvo tem de ser um caminho absoluto
        pai, fcb = self._nova_entrada(processo, caminho, "simbolico")
        fcb.alvo = alvo
        self._gravar_diretorio(pai)

    def percorrer(self, processo, caminho):
        """Percorre a árvore a partir de `caminho` (como um `find`), lendo cada diretório
        visitado (1 leitura) e seguindo links simbólicos para diretórios. Não pode entrar em
        laço: ao chegar de novo a um diretório já visitado nesta travessia, conte
        self.contadores['ciclos_detectados'] e não desça. Contem também
        'percorrer_diretorios', 'percorrer_arquivos' e 'links_pendentes' (links simbólicos
        cujo alvo não existe; contem também os encontrados pelo open)."""
        c = self.contadores
        visitados = set()

        def visitar(d):
            if d.id in visitados:
                c["ciclos_detectados"] += 1
                return
            visitados.add(d.id)
            self._ler_diretorio(d)
            c["percorrer_diretorios"] += 1
            for fid in list(d.entradas.values()):
                f = self.fcbs[fid]
                if f.tipo == "simbolico":
                    try:
                        f = self.resolver(f.alvo)
                    except ErroSA as e:
                        c["links_pendentes" if e.codigo == "ENOENT" else "links_em_laco"] += 1
                        continue
                if f.tipo == "diretorio":
                    visitar(f)
                else:
                    c["percorrer_arquivos"] += 1

        inicio = self.resolver(caminho)
        if inicio.tipo != "diretorio":
            c["percorrer_arquivos"] += 1
            return
        visitar(inicio)

    # ============================================================ módulo M5
    PERMISSAO_EXIGIDA = {"r": "r", "w": "w", "a": "w", "rw": "rw"}
    BITS = {"r": 4, "w": 2, "x": 1}

    def _verificar_permissao(self, processo, fcb, modo):
        usuario = self.usuario_de(processo)
        exigida = self.PERMISSAO_EXIGIDA[modo]
        if usuario == "root":
            self.contadores["verificacoes"] += 1
            return
        for pos, (quem, perm) in enumerate(fcb.acl, start=1):
            if quem == usuario:
                self.contadores["verificacoes"] += pos
                permitido = all(p in perm for p in exigida)
                break
        else:
            self.contadores["verificacoes"] += len(fcb.acl) + 1
            bits = (fcb.permissoes >> 6) & 7 if usuario == fcb.dono else fcb.permissoes & 7
            permitido = all(bits & self.BITS[p] for p in exigida)
        if not permitido:
            self.contadores["negados"] += 1
            raise ErroSA("EACCES", "%s não pode abrir %s em '%s'" % (usuario, fcb.nome, modo))

    def _verificar_trava(self, processo, fcb):
        g = self.tabela_global.get(fcb.id)
        if g is None or g.trava is None or g.trava == processo:
            return
        if self.bloqueio == "mandatorio":
            self.contadores["bloqueados_mandatorio"] += 1
            raise ErroSA("EBUSY", "%s está travado por %s" % (fcb.nome, g.trava))
        self.contadores["violacoes_consultivo"] += 1

    def usuario(self, processo, nome):
        """Associa o processo a um usuário (nenhum acesso ao disco)."""
        self.usuarios[processo] = str(nome)

    def _alterar_protecao(self, processo, caminho):
        """Comum a chmod e setacl: lê o caminho, confere o dono e devolve o FCB."""
        fcb = self.resolver(caminho)
        usuario = self.usuario_de(processo)
        if usuario not in ("root", fcb.dono):
            raise ErroSA("EPERM", "%s não é dono de %s" % (usuario, caminho))
        return fcb

    def _gravar_pai(self, fcb):
        self._gravar_diretorio(self.fcbs[fcb.pai] if fcb.pai is not None else fcb)

    def chmod(self, processo, caminho, permissoes):
        """Lê os diretórios até o arquivo; só o dono ou root (senão EPERM); grava o bloco do
        pai (1 escrita). permissoes em octal, como texto (ex.: '640')."""
        try:
            valor = int(str(permissoes), 8)
        except ValueError:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        fcb = self._alterar_protecao(processo, caminho)
        fcb.permissoes = valor
        self._gravar_pai(fcb)

    def setacl(self, processo, caminho, usuario, permissoes):
        """Como o chmod, mas acrescenta (ou substitui) a entrada (usuario, 'rwx') na ACL."""
        fcb = self._alterar_protecao(processo, caminho)
        usuario, permissoes = str(usuario), str(permissoes)
        for i, (quem, _) in enumerate(fcb.acl):
            if quem == usuario:
                fcb.acl[i] = (usuario, permissoes)
                break
        else:
            fcb.acl.append((usuario, permissoes))
        self._gravar_pai(fcb)

    def lock(self, processo, handle):
        """Trava exclusiva do arquivo inteiro. Se outro processo detém a trava: EBUSY
        (conte 'travas_negadas'); senão conte 'travas_concedidas'."""
        g = self.tabela_global[self._entrada(processo, handle).fcb_id]
        if g.trava is not None and g.trava != processo:
            self.contadores["travas_negadas"] += 1
            raise ErroSA("EBUSY", "travado por %s" % g.trava)
        g.trava = processo
        self.contadores["travas_concedidas"] += 1

    def unlock(self, processo, handle):
        g = self.tabela_global[self._entrada(processo, handle).fcb_id]
        if g.trava is not None and g.trava != processo:
            raise ErroSA("EPERM", "a trava é de %s" % g.trava)
        g.trava = None
