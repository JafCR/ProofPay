# ProofPay · Guion del video (≤ 4 min)

> Voz: Jaf, en inglés. Acotaciones en español. Duración objetivo: **3:50**.
> Requisitos del concurso cubiertos: ≤4 min, público, inglés, **consola de GCP visible** (escena 3).
> Asume que la misión larga liquidó RELEASED (entrega: jue 28 ago, 15:00 UTC / 9:00 am CR).

## Escenas

### 1 · El problema — 0:00–0:25 (25s)

**Pantalla:** fondo negro o thumbnail; a los ~10s, la home del demo (`proofpay-agent…run.app`) ya cargada.

> We keep saying AI agents will hire real businesses and pay them. But would you
> give an AI your wallet? One hallucination and your money is gone.
>
> This is ProofPay: an autonomous procurement agent that hires a real law firm,
> pays real money — and structurally cannot be scammed, because the AI is never
> allowed to touch the money.

### 2 · Qué es, en una frase — 0:25–0:45 (20s)

**Pantalla:** `docs/architecture.png` (el mismo diagrama que ven los judges en Devpost y el README). NO entrar en detalle de cajas; un solo paneo.

> Under the hood it's a Gemini agent on Google Cloud, hiring through an open
> marketplace with escrow and staked collateral. The architecture rule that
> matters is this one: **the AI can veto a payment, but only deterministic
> code — five auditable checks — can release one.** Everything else is plumbing.
> Let me just show you.

### 3 · Demo happy, en vivo y sin cortes — 0:45–2:05 (80s)

**Pantalla:** mitad izquierda la web del demo, mitad derecha la consola de GCP con los logs del servicio Cloud Run (`proofpay-agent`) — **este es el plano que cumple el requisito de GCP visible**. Click en "Start this mission" (restaurante en Tamarindo). Durante los ~45s de "trabajo" del proveedor, la voz sigue narrando sobre el wake log en vivo; no cortar.

> I'm starting a real mission: set up a beachfront restaurant company in Costa
> Rica, budget five thousand dollars.
>
> Wake one: the agent searches the marketplace, gets eight offers, and Gemini
> picks one — a vetted law firm with collateral at stake. It signs the contract,
> funds escrow… and now watch the Cloud Run logs on the right: the request
> *ends*. The agent isn't polling. It's not even running. It's asleep.
>
> The law firm is doing the work — in this demo, about forty-five seconds.
>
> There it is. Delivery fires a Pub/Sub event, and that's a brand-new request
> waking the agent up. It re-verifies every registry certificate at the source —
> not trusting the provider, not trusting the platform — runs the five release
> checks, all green… and pays. Escrow released. End to end, about two minutes,
> no human in the loop.

### 4 · La misión larga — 2:05–3:00 (55s) ⭐

**Pantalla:** trace de la misión `4f869f2e…` ya liquidada. Empezar con zoom al **wake log**: los gaps de horas entre wakes son el plano más importante del video. Luego un plano rápido de Cloud Scheduler (`proofpay-sweep`, cada 6 horas).

> Two minutes is a demo. Real procurement takes days. So two days ago, the agent
> hired a second law firm — and then it went to sleep *for real*.
>
> Look at this wake log. These gaps between wakes aren't seconds — they're
> **six hours**. A scheduled sweep briefly wakes the agent, it checks the
> engagement — no delivery yet — and goes back to sleep. No process running, no
> state in memory. The whole mission lives in a persisted trace in Firestore.
>
> Thirty-six hours later the firm finally delivered. The delivery event woke
> the agent one last time — quite possibly on a Cloud Run instance that never
> met the buyer — it re-verified every proof and released the payment. On its
> own. While I was asleep too.

### 5 · Demo fraud: el agente que dice NO — 3:00–3:40 (40s)

**Pantalla:** trace terminado de la misión DISPUTED (`76335c19…`), curado en la home. Zoom al check **P2 en rojo** y al 404, luego al slashing del stake. (Alternativa si hay tiempo de sobra: correrlo en vivo con un corte durante los 45s de espera, rotulado "45s trimmed".)

> Now the important question: what happens with a scammer?
>
> Here a provider submitted a complete-looking filing, and the platform verified
> it at submission time. But before payment, one certificate was quietly annulled
> at the government registry. At payment time, the agent re-checked every
> reference itself — and got a 404. Check P-2 failed. No payment. Dispute opened,
> buyer refunded, and one thousand dollars slashed from the provider's stake.
>
> The LLM never had the authority to override that. It can say no; it can never
> force a yes.

### 6 · Cierre — 3:40–3:55 (15s)

**Pantalla:** home del demo con las misiones curadas; URL grande en pantalla.

> ProofPay: an agent you can hand a budget to, and sleep. Built on Gemini and
> Google Cloud for the All Things Agentic Hackathon. The demo is live — link
> below. Go start a mission and watch it say no to a fraud in real time.

## Checklist de grabación

**Hoy (mié 27):**
- [ ] Grabar escena 3 (demo happy en vivo + consola GCP). Se puede repetir hasta que salga limpia; cada corrida son ~2 min.
- [ ] Grabar escena 5 (trace DISPUTED) y planos de la home para 1, 2 y 6.
- [ ] Dejar lista la pantalla: tema, zoom ~125%, ocultar bookmarks/tabs personales.

**Mañana (jue 28, después de ~9:05 am CR):**
- [ ] Verificar que la misión larga quedó RELEASED.
- [ ] Grabar escena 4: wake log completo con los gaps de horas + plano de Cloud Scheduler.
- [ ] Llenar los `[CONFIRMAR]` del Project Story con el dato real (~36 h).

**Si la entrega de mañana falla:** correr el fallback local anotado en DECISIONS.md y grabar después de que liquide; el guion no cambia.
