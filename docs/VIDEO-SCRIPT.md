# ProofPay · Guion del video (versión simple, ~3 min)

> Voz: Jaf, en inglés simple. Frases cortas. Si te trabas en una línea, dila con tus palabras.
> Requisito del concurso cubierto: consola de GCP visible (escena 2).

## Escena 1 · El gancho — 20s

**Pantalla:** la home del demo.

> Would you give an AI your wallet?
>
> This is ProofPay. It's an agent that hires a real business, holds the
> money in escrow, and only pays after checking the proof itself.
>
> One rule makes it safe: the AI can say NO to a payment. But only code —
> five simple checks — can say YES. Let me show you.

## Escena 2 · Demo en vivo — 90s

**Pantalla:** mitad izquierda la web del demo, mitad derecha Logs Explorer con el filtro de POSTs ("Last 5 minutes", usar "Re-run query" o "Extend time" para refrescar). Click en "Start this mission".

> I'm hiring a law firm to set up a company in Costa Rica. Watch.
>
> First request: the agent picks a firm, signs the contract, and locks the
> money in escrow. Then the request ends. Look at the logs — nothing is
> running. The agent is asleep.
>
> *(cuando aparezca el POST /sweep, si aparece)* That tiny request? The agent
> woke up, saw no delivery yet, and went back to sleep in one second.
>
> *(cuando la web muestre el pago)* The firm delivered. One last request
> wakes the agent. It checks every certificate against the government
> registry — itself — and pays. Three requests. That's the whole agent.

## Escena 3 · Las 40 horas — 30s

**Pantalla:** trace `?mission=4f869f2e…` — zoom al wake log, a los gaps de horas.

> That demo took two minutes. This mission took forty hours.
>
> Same agent. It slept for real — these gaps between wakes are six HOURS,
> not seconds. When the firm finally delivered, it woke up, checked
> everything, and paid. While I was asleep too.

## Escena 4 · El fraude — 30s

**Pantalla:** trace `?mission=76335c19…` — zoom al check P2 en rojo y al 404.

> And when someone tried to cheat?
>
> A provider submitted papers that looked perfect. But one certificate had
> been cancelled at the registry. The agent re-checked it, got a 404, and
> refused to pay. The provider lost a thousand dollars of his own stake.

## Escena 5 · Cierre — 15s

**Pantalla:** la home, URL visible.

> An agent you can trust with a budget — because it knows how to say no.
> Built with Gemini on Google Cloud. Try it yourself — the link is below.

## Checklist

- [ ] Ventana de incógnito para la web (pantalla limpia, sin pins).
- [ ] Logs Explorer listo: query de POSTs + "Last 5 minutes".
- [ ] Grabar escena 2 (repetir hasta que salga limpia; cada intento ~2 min).
- [ ] Escenas 1, 3, 4, 5: son solo pantalla + voz, sin nada en vivo.
- [ ] Editar, subir a YouTube público, pegar el link en Devpost.
