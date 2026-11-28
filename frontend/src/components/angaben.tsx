"use client";

// Steuern › one tax year › Eure Angaben as topics (phase 10 §7.8 (b) part 2, D294, mockup 22c).
//
// ?tab=angaben is the topic list (frame 2): on top „Aus euren Konten: N Vorschläge“ with „Der Reihe
// nach prüfen“ (the booked proposals of §7.8 (a), each confirmed through POST /return-inputs with
// `from_proposal`, a 409 return_input.proposal_stale reloading the guide), then the seven topics of
// GET /tax/{year}/guide in the server's order, each with its state and one line worded from the
// server's facts; the browser computes no count and no state. ?thema=<topic> opens one topic
// (frames 3–7): its questions one at a time through the Jahresfragen flow (questionnaire.tsx, the
// same POST /tax/questionnaire/answers), a booked proposal as the question's pre-filled answer,
// then the topic's entries (the input blocks of returnInputs.tsx and householdReturn.tsx, the pay
// inputs of income.tsx). Every field below the FieldHelpProvider explains itself (fieldHelp.tsx).
//
// Old addresses: `#eingabe-<kind>`, `#arbeitslohn`, `#kinder`, `#haushalt` and `#tax-inputs-title`
// open their topic (topicOfAnchor), `#tax-price-renovation` the Immobilie step, and
// /tax/<year>/fragen forwards here (app/(app)/tax/[year]/fragen). Without topics (the year's
// questions cannot be read) the page is the one-page form of before (taxAngaben.tsx).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { DocumentDetail, Owner, StateCode } from "@/api/schemas";
import type { components } from "@/api/types";
import { useFormat, useT } from "@/i18n";
import { angaben as messages } from "@/i18n/angaben";
import { taxGuide as guideText } from "@/i18n/taxGuide";

import { ReadUpload, UploadControl, type Uploaded } from "./documents";
import { loadDirectory } from "./entities";
import { FieldHelpNote, FieldHelpProvider } from "./fieldHelp";
import { FormBanner, useFormErrors } from "./form";
import { HouseholdReturnBlocks } from "./householdReturn";
import { IncomeSection } from "./income";
import { STATE_CODE } from "./labels";
import { ProposalSection } from "./proposals";
import { QuestionnaireFlow, type SlotExtra } from "./questionnaire";
import { InputsPanel } from "./returnInputs";
import { ReturnInputsSync, useReturnInputsSync } from "./returnInputsSync";
import { TaxInputsHead, TaxInputsScreen } from "./taxAngaben";
import { AngabenPoints, PaymentsDialog, ProposalForm, proposalTexts, useGuide, useOpenAnchor } from "./taxGuide";
import {
  angabenTopicHref,
  propertyAnchorHref,
  taxFormHref,
  taxStepHref,
  topicOfAnchor,
  type AngabenTopic,
  type TaxYearState,
} from "./taxYear";
import { ActionDialog, Loading, Notice } from "./ui";
import { useLoad } from "./useLoad";

import "./tax.css";

type TaxGuide = components["schemas"]["TaxGuide"];
type GuideTopic = components["schemas"]["GuideTopic"];
type BookedProposal = components["schemas"]["BookedProposal"];

const TOPICS: readonly AngabenTopic[] = ["couple", "pay", "work_costs", "insurance", "benefits", "children", "household"];

export function isTopic(value: string | null | undefined): value is AngabenTopic {
  return value != null && (TOPICS as readonly string[]).includes(value);
}

/** The question a booked proposal answers (rules/angaben_topics.py PROPOSAL_TOPIC's questions):
 * the proposal is shown as that question's pre-filled answer (22c frame 3). */
const PROPOSAL_QUESTION: Readonly<Record<string, string>> = {
  benefit: "owner.replacement_income",
  donation: "household.donations",
  childcare: "household.childcare",
  household_services: "household.services_35a",
};

/** The return-input kinds of a topic the owner's input blocks show (rules/angaben_topics.py
 * TOPIC_KINDS; the household's kinds are householdReturn.tsx's blocks). */
const OWNER_KINDS: Partial<Record<AngabenTopic, readonly string[]>> = {
  work_costs: ["commute", "home_office", "work_room", "work_equipment", "telecom", "professional_association", "training", "other_expense"],
  insurance: ["other_insurance", "vorsorge_override"],
  benefits: ["replacement_income"],
};

const loadOwners = () => ok(api.GET("/api/owners"));

function proposalKey(proposal: BookedProposal): string {
  return `${proposal.group}:${proposal.owner_id ?? ""}:${proposal.payee ?? ""}:${[...proposal.allocation_ids].sort().join(",")}`;
}

function proposalsOf(guide: TaxGuide): BookedProposal[] {
  return (guide.points ?? []).flatMap((p) => (p.step === "angaben" && p.proposal ? [p.proposal] : []));
}

export function AngabenScreen({
  year,
  ownerParam,
  propertyParam,
  topicParam,
  frage,
}: {
  year: number;
  ownerParam: string | null;
  propertyParam: string | null;
  topicParam: string | null;
  frage: string | null;
}) {
  // One version for the guide's proposals, the questions and the typing blocks
  // (components/returnInputsSync.ts).
  const [version, setVersion] = useState(0);
  const sync = useMemo(() => ({ version, bump: () => setVersion((n) => n + 1), active: true }), [version]);
  return (
    <ReturnInputsSync.Provider value={sync}>
      <FieldHelpProvider year={year}>
        <AngabenBody year={year} ownerParam={ownerParam} propertyParam={propertyParam} topicParam={topicParam} frage={frage} />
      </FieldHelpProvider>
    </ReturnInputsSync.Provider>
  );
}

function AngabenBody({
  year,
  ownerParam,
  propertyParam,
  topicParam,
  frage,
}: {
  year: number;
  ownerParam: string | null;
  propertyParam: string | null;
  topicParam: string | null;
  frage: string | null;
}) {
  const router = useRouter();
  const owners = useLoad(loadOwners);
  const guide = useGuide(year);
  const changed = useReturnInputsSync(guide.reload);
  const topic = isTopic(topicParam) ? topicParam : null;
  useOpenAnchor();
  // A plain answer reads only its topic's questions again (phase 10 §7.13 (c) 3, D315), so the
  // topic list's states are read once the topic is left, not after every answer.
  const shownTopic = useRef(topic);
  const reloadGuide = guide.reload;
  useEffect(() => {
    if (shownTopic.current === topic) return;
    shownTopic.current = topic;
    reloadGuide();
  }, [topic, reloadGuide]);

  // An old anchor of the one-page Eure Angaben opens where its form is now.
  useEffect(() => {
    function route() {
      const hash = window.location.hash;
      if (!hash || hash.startsWith("#d-")) return;
      // The price renovation card (phase 3 D287) is on the Immobilie step now; its per-property
      // anchor `#tax-price-renovation-<id>` names the property.
      const renovation = /^#tax-price-renovation-(.+)$/.exec(hash);
      const property = renovation
        ? `${taxStepHref(year, "immobilie", renovation[1] ?? null)}${hash}`
        : propertyAnchorHref(year, propertyParam, hash);
      if (property) {
        router.replace(property);
        return;
      }
      if (topic) return;
      const named = topicOfAnchor(hash);
      if (named) router.replace(`${angabenTopicHref(year, named, { ownerId: ownerParam })}${hash}`);
    }
    route();
    window.addEventListener("hashchange", route);
    return () => window.removeEventListener("hashchange", route);
  }, [year, propertyParam, ownerParam, topic, router]);

  return (
    <Loading state={guide}>
      {(data) => {
        if (data.topics == null) {
          return <TaxInputsScreen year={year} ownerParam={ownerParam} propertyParam={propertyParam} />;
        }
        const list = owners.data ?? [];
        const state: TaxYearState = { year, propertyId: propertyParam, ownerId: ownerParam };
        return (
          <>
            <TaxInputsHead state={state} />
            {topic ? (
              <TopicScreen
                key={topic}
                year={year}
                topic={topic}
                guide={data}
                owners={list}
                ownerParam={ownerParam}
                frage={frage}
                onOwnersChanged={owners.reload}
                onChanged={changed}
              />
            ) : (
              <TopicList year={year} guide={data} owners={list} onChanged={changed} />
            )}
          </>
        );
      }}
    </Loading>
  );
}

/** Opens and scrolls to the element the URL's hash names once it exists: the topic's blocks load
 * after the page, so the browser's own jump (and useOpenAnchor's, on mount) finds nothing. */
function useHashTarget(ready: unknown) {
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id || id.startsWith("d-")) return;
    const target = document.getElementById(id);
    if (!target) return;
    if (target instanceof HTMLDetailsElement) target.open = true;
    target.scrollIntoView({ block: "start" });
  }, [ready]);
}

// --- the topic list (frame 2) ---------------------------------------------------------------

function TopicList({ year, guide, owners, onChanged }: { year: number; guide: TaxGuide; owners: readonly Owner[]; onChanged: () => void }) {
  const a = useT(messages);
  const proposals = proposalsOf(guide);
  // Once shown, the box stays on the page, so confirming the last proposal in the walk does not
  // unmount its dialog before „Alle Vorschläge sind geprüft“.
  const [shown, setShown] = useState(proposals.length > 0);
  if (proposals.length > 0 && !shown) setShown(true);
  return (
    <div className="stack angaben">
      {shown ? <ProposalsBox proposals={proposals} owners={owners} onChanged={onChanged} /> : null}
      <section className="card stack" aria-labelledby="angaben-topics">
        <h2 id="angaben-topics">{a.topicsLabel}</h2>
        <ul className="angaben-topics">
          {(guide.topics ?? []).map((topic) => (
            <li key={topic.topic}>
              <TopicRow year={year} topic={topic} owners={owners} />
            </li>
          ))}
        </ul>
      </section>
      {/* The guide's points of this step that belong to no topic (none today; a new rule's
          point stays visible here until it is given one). */}
      <AngabenPoints year={year} topic={null} />
    </div>
  );
}

/** The questionnaire's Steuerklasse code as printed (questionnaire/2026.yaml's labels). */
function taxClassLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return code === "iv_factor" ? "IV + Faktor" : code.toUpperCase();
}

function TopicRow({ year, topic, owners }: { year: number; topic: GuideTopic; owners: readonly Owner[] }) {
  const a = useT(messages);
  const f = useFormat();
  const name = (id: string) => owners.find((o) => o.id === id)?.full_name ?? "";
  let line: string;
  if (topic.topic === "couple") {
    line = (topic.owners ?? [])
      .map((o) =>
        o.tax_state
          ? a.churchLine(name(o.owner_id), o.church_tax === true, STATE_CODE[o.tax_state as StateCode] ?? o.tax_state, taxClassLabel(o.tax_class))
          : a.missingState(name(o.owner_id)),
      )
      .join(" · ");
  } else if (topic.topic === "pay") {
    line = (topic.owners ?? [])
      .map((o) =>
        o.gross_source ? a.payLine(name(o.owner_id), o.gross ? f.money(o.gross) : null, a.payFrom(o.gross_source)) : a.payNone(name(o.owner_id)),
      )
      .join(" · ");
  } else if (topic.proposals > 0) {
    line = a.proposalsOpen(topic.proposals);
  } else if (topic.questions_open > 0) {
    line = a.questionsOpen(topic.questions_open);
  } else if (topic.state === "done" && topic.questions_total > 0) {
    line = a.questionsAnswered(topic.questions_total);
  } else {
    line = a.nothingYet;
  }
  const stateText = topic.state === "done" ? a.stateDone : topic.state === "not_needed" ? a.stateNotNeeded : a.stateOpen(topic.open);
  return (
    <Link href={angabenTopicHref(year, topic.topic)} className="detail-row angaben-topic">
      <span className="angaben-topic-head">
        <strong>{a.topic(topic.topic)}</strong>
        <span className={topic.state === "done" ? "badge" : topic.state === "not_needed" ? "badge badge-muted" : "badge badge-warn"}>
          {topic.state === "done" ? "✓ " : null}
          {stateText}
        </span>
      </span>
      {line ? <span className="muted angaben-topic-line">{line}</span> : null}
    </Link>
  );
}

/** „Aus euren Konten: N Vorschläge“ and „Der Reihe nach prüfen“: the proposals one at a time,
 * each through the same ProposalForm as the to-do's „Übernehmen“ (taxGuide.tsx). */
function ProposalsBox({ proposals, owners, onChanged }: { proposals: BookedProposal[]; owners: readonly Owner[]; onChanged: () => void }) {
  const a = useT(messages);
  const t = useT(guideText);
  const f = useFormat();
  const line = proposals.map((p) => `${proposalTexts(p, t)[0]}`).join(" · ");
  return (
    <section className="card stack angaben-proposals" aria-labelledby="angaben-proposals">
      <h2 id="angaben-proposals">{proposals.length > 0 ? a.proposalsTitle(proposals.length) : a.proposalsDone}</h2>
      {proposals.length > 0 ? <p className="muted">{line || a.proposalsLine}</p> : null}
      <div className="row" hidden={proposals.length === 0}>
        <ActionDialog label={a.proposalsWalk} title={a.proposalsWalk} buttonClass="">
          {(close) => <ProposalWalk proposals={proposals} owners={owners} onChanged={onChanged} onClose={close} money={f.money} />}
        </ActionDialog>
      </div>
    </section>
  );
}

function ProposalWalk({
  proposals,
  owners,
  onChanged,
  onClose,
}: {
  proposals: BookedProposal[];
  owners: readonly Owner[];
  onChanged: () => void;
  onClose: () => void;
  money: (value: string) => string;
}) {
  const a = useT(messages);
  const t = useT(guideText);
  // A snapshot of the proposals when the walk began: a reload of the guide (after each save)
  // must not move the walk under the person.
  const [list] = useState(() => proposals);
  const [at, setAt] = useState(0);
  const current = list[at];
  if (!current) {
    return (
      <div className="stack">
        <p>{a.proposalsDone}</p>
        <div className="row">
          <button type="button" className="secondary" onClick={onClose}>
            {a.walkClose}
          </button>
        </div>
      </div>
    );
  }
  const [title, text] = proposalTexts(current, t);
  const next = () => setAt((n) => n + 1);
  return (
    <div className="stack" key={proposalKey(current)}>
      <p className="muted">{a.proposalOf(at + 1, list.length)}</p>
      <p>
        <strong>{title}</strong> <span className="angaben-source">{a.fromAccounts}</span>
      </p>
      <p className="muted">{text}</p>
      <ProposalExtras proposal={current} onChanged={onChanged} />
      <ProposalForm
        proposal={current}
        owners={owners}
        onDone={() => {
          onChanged();
          next();
        }}
        onStale={() => {
          // The proposal was out of date: the server wrote nothing; the guide reloads and the
          // walk moves on.
          onChanged();
          next();
        }}
        onCancel={next}
        cancelLabel={a.proposalLater}
      />
    </div>
  );
}

/** What a proposal shows beside its form: its payments, and the upload a donation above 300 €
 * (`receipt_needed`, decided by the server) or a household service's invoice needs. */
function ProposalExtras({ proposal, onChanged }: { proposal: BookedProposal; onChanged: () => void }) {
  const a = useT(messages);
  const t = useT(guideText);
  return (
    <>
      <PaymentsDialog proposal={proposal} />
      {proposal.group === "donation" ? <p className="muted">{proposal.receipt_needed ? a.receiptAbove : a.receiptBelow}</p> : null}
      {proposal.receipt_needed ? (
        <ReadUpload label={proposal.group === "donation" ? a.receiptUpload : t.uploadReceipt} onUploaded={onChanged} />
      ) : null}
      {proposal.group === "household_services" ? (
        <>
          <p className="muted">{a.servicesLabour}</p>
          <ReadUpload label={t.uploadInvoice} onUploaded={onChanged} />
        </>
      ) : null}
    </>
  );
}

// --- one topic (frames 3–7) -----------------------------------------------------------------

function TopicScreen({
  year,
  topic,
  guide,
  owners,
  ownerParam,
  frage,
  onOwnersChanged,
  onChanged,
}: {
  year: number;
  topic: AngabenTopic;
  guide: TaxGuide;
  owners: readonly Owner[];
  ownerParam: string | null;
  frage: string | null;
  onOwnersChanged: () => void;
  onChanged: () => void;
}) {
  const a = useT(messages);
  const proposals = proposalsOf(guide).filter((p) => PROPOSAL_QUESTION[p.group] !== undefined);
  const mine = proposals.filter((p) => topicOfProposal(p) === topic);
  // Opened at the question asked for, else at the first question a proposal pre-fills, else at
  // the first open one (the flow's own default).
  const first = mine[0];
  const start = frage
    ? { key: frage, ownerId: ownerParam }
    : first
      ? { key: PROPOSAL_QUESTION[first.group]!, ownerId: first.group === "benefit" ? (first.owner_id ?? null) : null }
      : null;
  const extra = useCallback(
    (slot: SlotExtra) => <SlotProposals key={`${slot.slot.key}:${slot.slot.owner_id ?? ""}`} extra={slot} proposals={mine} owners={owners} onChanged={onChanged} />,
    [mine, owners, onChanged],
  );
  const kinds = OWNER_KINDS[topic];
  useHashTarget(topic);
  // Phase 10 §7.13 (c) 1, D315: the end of a topic offers the next one and waits for the click
  // (Luis: „Wait to click next“); the last topic leads back to the list.
  const order = (guide.topics ?? []).map((t) => t.topic).filter(isTopic);
  const following = order[order.indexOf(topic) + 1] ?? null;
  const own = (guide.topics ?? []).find((t) => t.topic === topic);
  const end = (
    <Link
      className="button-link"
      href={following ? angabenTopicHref(year, following) : taxFormHref("angaben", { year, propertyId: null, ownerId: null })}
    >
      {following ? a.nextTopic(a.topic(following)) : a.allTopics}
    </Link>
  );
  return (
    <div className="stack angaben angaben-topic-screen">
      <p className="fragen-back">
        <Link href={taxFormHref("angaben", { year, propertyId: null, ownerId: null })}>{a.back}</Link>
      </p>
      <h2>{a.topic(topic)}</h2>
      {topic === "couple" ? (
        owners.map((owner) => (
          <OwnerTaxCard
            key={owner.id}
            owner={owner}
            onSaved={() => {
              onOwnersChanged();
              onChanged();
            }}
          />
        ))
      ) : null}
      <QuestionnaireFlow year={year} topic={topic} embedded start={start} extra={extra} end={end} onAnswered={(rows) => (rows ? onChanged() : undefined)} />
      <AngabenPoints year={year} topic={topic} />
      {topic === "pay" ? <PayEntries year={year} owners={owners} onChanged={onChanged} /> : null}
      {kinds ? <OwnerEntries year={year} topic={topic} kinds={kinds} owners={owners} ownerParam={ownerParam} /> : null}
      {topic === "children" || topic === "household" ? (
        <section className="card stack" aria-labelledby="angaben-entries">
          <h3 id="angaben-entries">{a.otherEntries}</h3>
          <p className="muted">{a.otherEntriesLine}</p>
          <HouseholdReturnBlocks year={year} part={topic} />
        </section>
      ) : null}
      {/* A topic without questions (its entries only) ends here instead of in the flow's summary. */}
      {own && own.questions_total === 0 ? <div className="row">{end}</div> : null}
    </div>
  );
}

function topicOfProposal(proposal: BookedProposal): AngabenTopic | null {
  if (proposal.group === "benefit") return "benefits";
  if (proposal.group === "childcare") return "children";
  if (proposal.group === "donation" || proposal.group === "household_services") return "household";
  return null;
}

/** A question's pre-filled answer from the bookings (frame 3; frame 5 per donation recipient):
 * „aus euren Konten“, its payments, „Ja, übernehmen“ and „Anders eintragen“. Each proposal is
 * saved through ProposalForm (POST /return-inputs with from_proposal); the last one answers the
 * question „Ja“, since its row is written. */
function SlotProposals({
  extra,
  proposals,
  owners,
  onChanged,
}: {
  extra: SlotExtra;
  proposals: readonly BookedProposal[];
  owners: readonly Owner[];
  onChanged: () => void;
}) {
  const a = useT(messages);
  const t = useT(guideText);
  const f = useFormat();
  const [taken, setTaken] = useState<readonly string[]>([]);
  // A benefit booked on a joint account names no owner: it is offered on each owner's question,
  // and ProposalForm asks whose it is.
  const matching = proposals.filter(
    (p) =>
      PROPOSAL_QUESTION[p.group] === extra.slot.key &&
      (p.group !== "benefit" || p.owner_id == null || p.owner_id === (extra.slot.owner_id ?? null)),
  );
  const left = matching.filter((p) => !taken.includes(proposalKey(p)));
  const current = left[0];
  if (matching.length === 0 || !current) return null;
  const [title, text] = proposalTexts(current, t);
  return (
    <div className="card stack angaben-prefill">
      {matching.length > 1 ? (
        <ul className="angaben-recipients" aria-label={a.donationsList}>
          {matching.map((p) => (
            <li key={proposalKey(p)} className="row">
              <span>{p.payee ?? "–"}</span>
              <span className="spacer" />
              <span>{f.money(p.difference)}</span>
              <span className="angaben-source">{taken.includes(proposalKey(p)) ? a.payAlready : a.fromAccounts}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p>
        <strong>{title}</strong> <span className="angaben-source">{a.fromAccounts}</span>
      </p>
      <p className="muted">{text}</p>
      <ProposalExtras proposal={current} onChanged={onChanged} />
      <ProposalForm
        key={proposalKey(current)}
        proposal={current}
        owners={owners}
        submitLabel={a.takeYes}
        cancelLabel={a.enterOther}
        onDone={() => {
          const rest = left.length - 1;
          setTaken((list) => [...list, proposalKey(current)]);
          onChanged();
          if (rest > 0) return;
          // Every row is written, so the question is answered: „Ja“ for a yes/no question; for
          // the benefit question (a choice) the proposal's benefit when it is one of the choices,
          // else the person picks it below.
          if (extra.question.answer_type !== "choice") extra.answer(null);
          else if (current.benefit && (extra.question.choices ?? []).some((c) => c.code === current.benefit)) extra.answer(current.benefit);
        }}
        onStale={onChanged}
        onCancel={extra.other}
      />
    </div>
  );
}

/** „Ihr beide“: church tax and Bundesland of one owner, two questions saved through the owner
 * command (PATCH /owners/{id}, the Eigentümer form's body); church tax needs the Bundesland. */
function OwnerTaxCard({ owner, onSaved }: { owner: Owner; onSaved: () => void }) {
  const a = useT(messages);
  const form = useFormErrors(["church_tax", "tax_state"]);
  const [church, setChurch] = useState<boolean | null>(owner.church_tax ?? null);
  const [state, setState] = useState<StateCode | "">(owner.tax_state ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const churchId = `angaben-church-${owner.id}`;
  const stateId = `angaben-state-${owner.id}`;
  async function save() {
    setBusy(true);
    setSaved(false);
    form.clear();
    try {
      await ok(
        api.PATCH("/api/owners/{owner_id}", {
          params: { path: { owner_id: owner.id } },
          body: {
            full_name: owner.full_name,
            tax_id_ref: owner.tax_id_ref ?? null,
            church_tax: church === true,
            tax_state: state === "" ? null : state,
          },
        }),
      );
      setSaved(true);
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card stack" aria-labelledby={`${churchId}-title`}>
      <h3 id={`${churchId}-title`}>{a.ownerQuestions(owner.full_name)}</h3>
      <fieldset className="stack">
        <legend id={churchId}>{a.churchAskOwner(owner.full_name)}</legend>
        <FieldHelpNote form="owner" field="church_tax" label={a.churchAskOwner(owner.full_name)} />
        <div className="fragen-answers" role="group" aria-labelledby={churchId}>
          <button type="button" className={church === true ? "" : "secondary"} aria-pressed={church === true} onClick={() => setChurch(true)}>
            {a.yes}
          </button>
          <button type="button" className={church === false ? "" : "secondary"} aria-pressed={church === false} onClick={() => setChurch(false)}>
            {a.no}
          </button>
        </div>
      </fieldset>
      <label htmlFor={stateId}>{a.stateAsk(owner.full_name)}</label>
      <FieldHelpNote form="owner" field="tax_state" label={a.stateAsk(owner.full_name)} />
      <select id={stateId} value={state} onChange={(e) => setState(e.target.value as StateCode | "")}>
        <option value="">{a.pick}</option>
        {(Object.entries(STATE_CODE) as [StateCode, string][]).map(([code, label]) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
      <FormBanner errors={form.errors} />
      <div className="row">
        <button type="button" disabled={busy || church === null || state === ""} onClick={() => void save()}>
          {busy ? a.saving : a.save}
        </button>
        {saved ? <span className="muted">{a.saved}</span> : null}
      </div>
    </section>
  );
}

/** Arbeitslohn, upload first (frames 4a, 4b): the Lohnsteuerbescheinigung read by the same
 * upload and suggestion form as on the document page (phase 4b step 2), its values shown with
 * their line and saved with „Stimmt so“; „Ohne Dokument eintragen“ opens the pay inputs
 * (income.tsx: expected gross pay, a Bescheid's zvE, a typed rate, „Kein Arbeitslohn“). */
function PayEntries({ year, owners, onChanged }: { year: number; owners: readonly Owner[]; onChanged: () => void }) {
  const a = useT(messages);
  const [uploaded, setUploaded] = useState<Uploaded | null>(null);
  return (
    <>
      <section className="card stack" aria-labelledby="angaben-pay-upload">
        <h3 id="angaben-pay-upload">{a.payUploadTitle}</h3>
        <p className="muted">{a.payUploadLine}</p>
        {uploaded ? (
          <ReadCertificate uploaded={uploaded} onApplied={onChanged} onAnother={() => setUploaded(null)} />
        ) : (
          <UploadControl label={a.payUpload} prefill onUploaded={setUploaded} />
        )}
      </section>
      <details id="arbeitslohn" className="card stack angaben-without">
        <summary>{a.payWithout}</summary>
        <IncomeSection owners={[...owners]} fixedYear={year} />
      </details>
    </>
  );
}

function ReadCertificate({ uploaded, onApplied, onAnother }: { uploaded: Uploaded; onApplied: () => void; onAnother: () => void }) {
  const a = useT(messages);
  const id = uploaded.document.id;
  const load = useCallback(() => ok(api.GET("/api/documents/{document_id}", { params: { path: { document_id: id } } })), [id]);
  const detail = useLoad(load);
  const directory = useLoad(loadDirectory);
  // The read runs as a job; ProposalSection polls it and shows its values once it settles.
  return (
    <div className="stack">
      <p className="muted">
        {a.payReadFrom} · <Link href={`/documents/${id}`}>{uploaded.document.original_filename ?? id}</Link>
      </p>
      {uploaded.aiUnavailable ? (
        <Notice>
          <Link href={`/documents/${id}`}>{uploaded.document.original_filename ?? id}</Link>
        </Notice>
      ) : (
        <Loading state={detail}>
          {(data: DocumentDetail) => (
            <ProposalSection
              detail={data}
              directory={directory.data ?? null}
              onApplied={() => {
                detail.reload();
                onApplied();
              }}
              applyLabel={a.stimmtSo}
            />
          )}
        </Loading>
      )}
      <div className="row">
        <button type="button" className="link" onClick={onAnother}>
          {a.payUpload}
        </button>
      </div>
    </div>
  );
}

/** One owner's input blocks of a topic (returnInputs.tsx InputsPanel, only the topic's kinds),
 * the owner picked by ?eigentuemer=…. Each block keeps its anchor `#eingabe-<kind>`. */
function OwnerEntries({
  year,
  topic,
  kinds,
  owners,
  ownerParam,
}: {
  year: number;
  topic: AngabenTopic;
  kinds: readonly string[];
  owners: readonly Owner[];
  ownerParam: string | null;
}) {
  const a = useT(messages);
  const owner = owners.find((o) => o.id === ownerParam) ?? owners[0] ?? null;
  const ownerId = owner?.id ?? "";
  const loadRows = useCallback(
    () => ok(api.GET("/api/return-inputs", { params: { query: { tax_year: year, owner_id: ownerId } } })),
    [ownerId, year],
  );
  const rows = useLoad(loadRows);
  const changed = useReturnInputsSync(rows.reload);
  useHashTarget(rows.data);
  if (!owner) return null;
  return (
    <section className="card stack" aria-labelledby="angaben-entries">
      <h3 id="angaben-entries">{a.otherEntries}</h3>
      {owners.length > 1 ? (
        <nav aria-label={a.pickOwner} className="row tax-owner-switch">
          {owners.map((o) => (
            <Link key={o.id} href={angabenTopicHref(year, topic, { ownerId: o.id })} aria-current={o.id === owner.id ? "page" : undefined} scroll={false}>
              {o.full_name}
            </Link>
          ))}
        </nav>
      ) : null}
      <p className="muted">{a.entriesFor(owner.full_name)}</p>
      <Loading state={rows}>
        {(list) => <InputsPanel key={owner.id} ownerId={owner.id} year={year} rows={list} onChanged={changed} kinds={kinds} />}
      </Loading>
    </section>
  );
}
