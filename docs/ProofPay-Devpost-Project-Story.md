# ProofPay · Devpost Project Story

> Todo lo de abajo va en el campo "About the project". Devpost renderiza Markdown y LaTeX.
> Está escrito en primera persona singular porque sos fundador solo, y eso juega a favor en el premio Individual/Hobbyist.
> Los `[CONFIRMAR]` son datos que solo vas a tener cuando el build esté corriendo. No los dejes puestos.

---

```markdown
## Inspiration

In 2026 every major payment rail shipped an agentic checkout. Stripe launched ACP, Google launched AP2, Visa and Mastercard launched their own. All of them answered the same question: how does an agent pay?

Nobody answered the question that comes first. If an agent is about to hire and pay for real work in the physical world, who should it trust, and on what evidence?

RentAHuman made the gap impossible to ignore. Y Combinator funded it, it collected around 500,000 signups in two weeks, and it proved the demand is real. Then the press documented what happens with no collateral and no verification: an empty marketplace, verification you could buy for $10 a month, and no penalty at all for fraud.

I spent the last three months building Pacta Protocol, an open source trust layer where a real registered business posts collateral, the buyer's money sits in escrow, and delivery is anchored to an official public registry. But Pacta is the supply side. Every buyer I had ever written was a deterministic script that always did the right thing.

This hackathon is the buyer. And it let me build the version that is actually hard.

An agent that can spend money is a demo. An agent that can refuse to spend it is infrastructure.

## What it does

ProofPay is an autonomous procurement agent. You give it a goal in plain language, for example "form a company in Costa Rica so I can buy land in Guanacaste and operate a hotel", and then you close the tab.

From there it runs on its own:

1. **Discovers.** It searches vetted providers and reads what each one has at stake. A provider with zero collateral is not a cheaper option, it is a different risk class, and ProofPay prices that difference itself.
2. **Contracts.** It drafts the engagement, locks the terms, and funds escrow. After the lock, nobody can edit the fulfillment steps, including me.
3. **Sleeps.** This is the part that matters. The process exits. Real professional work takes days, not seconds, and an agent that holds a socket open for a week is not autonomous, it is a hostage.
4. **Wakes on delivery.** When the provider submits, an event brings the agent back. It rehydrates its state and re-verifies every registry reference against the source, one by one, and checks that the kind of record returned actually matches what that step required. A valid document proving the wrong thing is still a failure.
5. **Decides.** It releases the payment, or it opens a dispute and explains why.

The demo runs the same goal twice. The first run ends with a $5,000 payment released against four verified registry references. In the second run the provider submits a forged reference. ProofPay catches it, refuses to pay, opens the dispute, and the provider's collateral gets slashed.

Same agent, same prompt, same tools. The only thing that changed was the evidence.

## How I built it

The whole system is one agent with a hard boundary between judgment and authority.

**Reasoning.** Gemini 3.5 Flash, running inside Google ADK. It handles the three genuinely ambiguous decisions: which offer to take given price and collateral at risk, whether a returned registry record satisfies the step that required it, and how to word a dispute so a human arbiter can rule on it.

**Tools.** ADK's `MCPToolset` pointed straight at Pacta's existing MCP server, which exposes the full lifecycle as 12 tools: discover, contract, escrow, verify, settle, dispute, rate. I wrote zero new protocol code for this hackathon. That was the point. Any MCP-capable agent can do what ProofPay does, which is exactly the claim the protocol makes.

**Runtime.** Cloud Run for the agent and for the marketplace, Firestore for engagement state and the decision log, Pub/Sub for the delivery event that wakes the agent, Cloud Scheduler for deadline sweeps on engagements that go quiet.

**The gate.** Money-moving calls sit behind a deterministic policy check that the model cannot talk its way past. Release happens only when

$$\text{release} \iff \forall p \in P : \text{verified}(p) \wedge \text{kind}(p) = \text{required}(p)$$

where $P$ is the set of proofs submitted for the engagement and every verification in that set was performed in the current wake cycle against the source registry, not read from cache and not taken from the provider's own summary.

The model proposes. The runtime disposes. Gemini decides what the evidence means, and code decides whether money is allowed to move.

## Challenges I ran into

**Asynchrony is an architecture, not a flag.** My first version was a long-lived loop and it worked beautifully until the instance recycled. Rebuilding it so every wake is a cold process that reconstructs context from Firestore changed the shape of the whole agent. Nothing lives in memory between wakes, so everything the agent knows has to be something it can prove to itself again later.

**Evidence is an attack surface.** The provider controls the text of the delivery. The moment an LLM reads attacker-supplied content and that reading can move funds, prompt injection stops being an inconvenience and becomes a payment exploit. My answer is structural rather than a better prompt: the model's verdict is advisory and always paired with a machine check against the registry, and the release predicate above is evaluated in code on verification results the agent fetched itself. There is no sentence a provider can write that makes the escrow open.

**Knowing what not to hand the model.** Early on I let Gemini decide everything including whether to fund escrow. That was wrong. Funding is mechanical, it follows from the agreement being locked, so it belongs in the state machine. The model earns its place only where the question is genuinely a judgment call. Deciding that boundary took longer than writing either side of it.

**Making the failure legible.** A refusal is invisible by nature. Nothing happens, which is the whole point, and it makes for a terrible demo unless you build the trace. So every wake writes a reasoning record, and the dispute path surfaces the exact mismatch between what the step required and what the registry actually returned.

## Accomplishments that I am proud of

The agent refuses. It sees a plausible looking document, checks it against the state, finds it is not real, and keeps the money locked. That happens without a human in the loop and without me writing a rule for that specific document.

It also survives its own death. An engagement can start on one instance, sit idle for days, and settle on an instance that never met the buyer. The longest mission in the demo spans 40 hours of wall clock time across 10 wakes, and it started on one instance and settled on another.

And it is built entirely on tools any other developer can point at their own Pacta deployment today. Nothing here is a private fork.

## What I learned

That autonomy is mostly about what an agent is allowed to do, not what it is able to do. Capability was the easy half. Every hour I actually spent went into constraining the agent so that its power to move money stayed proportional to the evidence in front of it.

I also learned that verification and comprehension are different problems that look identical in a chat window. An LLM can read a document and tell you what it says. It cannot tell you the document exists. Those two facts only get connected by fetching the record from the authority that issued it, and once you accept that, the architecture designs itself.

## What's next

Real registry adapters. The proofs in this demo run against a marketplace instance, and the next milestone is the live adapter against Costa Rica's Registro Nacional, followed by the tourism stack of ICT, CCSS and Hacienda. That is the difference between a demo and infrastructure.

After that, ProofPay becomes the reference buyer that Pacta Protocol has been missing. Pacta already tells businesses how to be verifiable. This shows the other side of the market what a buyer that actually verifies looks like, and it ships as open source under MIT alongside the rest of the protocol.

**Live demo:** https://proofpay-agent-305908094913.us-central1.run.app/
**Code:** https://github.com/JafCR/ProofPay
**Protocol:** https://pactaprotocol.org
```

---

## Lo que falta antes de pegar

1. **URL de Cloud Run y del repo** al final.
2. **La corrida larga** en "Accomplishments". Si no dejás correr días de verdad, quitá esa frase entera en vez de inventar un número. Podés reemplazarla por el tiempo real que sí midas.
3. **El monto del happy path.** Puse $5,000 porque es el del demo de LandBridge. Si el seed de la hackathon usa otro, cambialo en "What it does".
4. **Gemini 3.5 Flash**: confirmá el nombre exacto del modelo que terminás usando. Las reglas piden 3.5 o más nuevo.

## Tres notas de criterio

- **El gancho está en la primera pantalla.** "An agent that can spend money is a demo. An agent that can refuse to spend it is infrastructure." Es la frase que quiero que el juez recuerde después de leer doscientos submissions, y por eso cierra Inspiration en vez de esconderse en el medio.
- **Los Challenges están escritos para el 30% de Architectural Discipline.** Prompt injection como exploit de pagos, el corte entre juicio del modelo y autoridad del código, y la reconstrucción de estado en frío son exactamente las tres cosas que ese criterio nombra: manejo de estado, credenciales y fallos. No son anécdotas, son argumentos.
- **La fórmula no es decoración.** Devpost soporta LaTeX y casi nadie lo usa. Una sola ecuación, que además dice algo real sobre el sistema, señala oficio sin gritar.
