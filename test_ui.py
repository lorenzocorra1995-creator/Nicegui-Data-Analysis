import asyncio
import pandas as pd
from nicegui import ui
from nicegui.testing import User
import app as A
import engine as eng
from demo_data import make_demo_df

DF = make_demo_df(5000)

CREATI = []
_orig_box = A.box_filtri
def _box(*a, **k):
    fb = _orig_box(*a, **k)
    CREATI.append(fb)
    return fb
A.box_filtri = _box

def setup_state():
    CREATI.clear()
    ui.page("/")(A.pagina)
    A.S.__init__()
    A.S.aggiungi("demo", DF.copy())

def rows_grid(user, n_atteso=None):
    return [len(g.options["rowData"]) for g in user.find(kind=ui.aggrid).elements]

async def test_apertura(user: User):
    setup_state()
    await user.open("/")
    for t in ["Data Explorer", "Applica filtri", "Group By", "Pivot", "Console", "Struttura colonne"]:
        await user.should_see(t)

async def test_filtri_annidati_via_ui(user: User):
    setup_state()
    await user.open("/")
    user.find(kind=ui.button, content="Condizione").click()
    user.find(kind=ui.button, content="Gruppo").click()
    await user.should_see("Colonna")
    # costruisce l'albero come l'utente (modificando i widget): regione=Nord AND (importo>1200 OR prodotto in A,B)
    fb = CREATI[0]  # filtro del tab Dati
    assert len(fb.tree["condizioni"]) == 2, fb.tree
    fb.tree["condizioni"][0].update(col="regione", op="=", val="Nord")
    grp = fb.tree["condizioni"][1]
    grp["condizioni"] = [eng.nuova_condizione("importo", ">", "1200"), eng.nuova_condizione("prodotto", "in lista", "A, B")]
    fb.render.refresh()
    user.find(marker="run-dati").click()
    await asyncio.sleep(0.5)
    atteso = len(DF[(DF.regione == "Nord") & ((DF.importo > 1200) | DF.prodotto.isin(["A", "B"]))])
    assert atteso in rows_grid(user), (atteso, rows_grid(user))

async def test_groupby_via_ui(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Group By").click()
    await user.should_see("Raggruppa per")
    user.find(kind=ui.button, content="Aggiungi valore").click()
    user.find(marker="run-groupby").click()
    await asyncio.sleep(1)
    # nessuna aggregazione valida e nessuna chiave -> 1 riga n_righe=5000
    assert 1 in rows_grid(user)

async def test_pivot_errore_gestito(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Pivot").click()
    user.find(marker="run-pivot").click()
    await asyncio.sleep(0.8)
    assert any("almeno" in str(m) for m in user.notify.messages), user.notify.messages

async def test_console_sql_e_python(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    ed = list(user.find(kind=ui.codemirror).elements)[0]
    ed.value = "SELECT regione, SUM(importo) AS tot FROM df GROUP BY 1 ORDER BY 2 DESC"
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    assert 3 in rows_grid(user), rows_grid(user)

    # Python: cambia linguaggio e usa df + sql()
    tog = [t for t in user.find(kind=ui.toggle).elements if "Python" in str(t.options)][0]
    tog.value = "Python"
    await asyncio.sleep(0.3)
    ed.value = "x = df[df.regione=='Sud']\nprint('righe Sud:', len(x))\nsql('select prodotto, count(*) n from df group by 1')"
    user.find(marker="run-console").click()
    await asyncio.sleep(1.5)
    assert 4 in rows_grid(user), rows_grid(user)
    out = [l.text for l in user.find(kind=ui.label).elements if "righe Sud" in (l.text or "")]
    assert out, "stdout non mostrato"

async def test_export_e_salva_dataset(user: User):
    setup_state()
    await user.open("/")
    user.find(content="Salva come dataset").click()
    await user.should_see("Nome")
    user.find(marker="salva-ok").click()
    await asyncio.sleep(0.3)
    assert len(A.S.datasets) == 2
    user.find(marker="dl-xlsx").click()
    await asyncio.sleep(1)
    assert len(user.notify.messages) == 1, user.notify.messages  # solo la notifica di 'Salva come dataset', nessun errore di export


async def test_ai_requires_key_and_writes_code(user: User, monkeypatch):
    setup_state()
    monkeypatch.setattr(A.ai, "generate_code", lambda *args: "SELECT 42")
    await user.open("/")
    user.find(content="Console").click()
    await user.should_see("Esegui")
    user.find(marker="ai-genera").click()
    await asyncio.sleep(0.3)
    assert any("API key" in str(m) for m in user.notify.messages), user.notify.messages

    A.S.api_key = "sk-test"
    list(user.find(kind=ui.textarea).elements)[0].value = "dammi 42"
    user.find(marker="ai-genera").click()
    await asyncio.sleep(0.8)
    assert list(user.find(kind=ui.codemirror).elements)[0].value == "SELECT 42"
