import { Fragment, useState, type FocusEvent, type FormEvent } from "react";
import "../shared.css";

type FixtureContext = "person" | "purpose";
type FixtureStructure = "semantic-first" | "presentation-first";
type FixtureId =
  | "person-context-semantic-first"
  | "person-context-presentation-first"
  | "purpose-context-semantic-first"
  | "purpose-context-presentation-first";

type FieldKind = "input" | "select" | "textarea";

type FieldDefinition = {
  index: number;
  label: string;
  kind?: FieldKind;
  inputMode?: "email" | "numeric" | "tel" | "text";
  options?: readonly string[];
};

type FieldGroup = {
  title: string;
  rows: readonly (readonly FieldDefinition[])[];
};

type PostalCompletion = {
  postalIndex: number;
  values: Readonly<Record<number, string>>;
};

type FormDefinition = {
  title: string;
  description: string;
  groups: readonly FieldGroup[];
  postalCompletions: readonly PostalCompletion[];
};

type FormGroupMembershipFixtureProps = {
  context: FixtureContext;
  experimentId?: string;
  fixtureId: FixtureId;
  structure: FixtureStructure;
};

const countryOptions = ["", "日本"] as const;
const prefectureOptions = ["", "東京都", "大阪府"] as const;

const personDefinition: FormDefinition = {
  title: "連絡先情報の登録",
  description: "本人情報と緊急連絡先を入力してください。",
  groups: [
    {
      title: "あなたの情報",
      rows: [
        [field(1, "姓"), field(2, "名")],
        [field(3, "姓（カナ）"), field(4, "名（カナ）")],
        [field(5, "メールアドレス", "input", "email")],
        [field(6, "電話番号", "input", "tel")],
        [field(7, "郵便番号", "input", "numeric")],
        [field(8, "国", "select", undefined, countryOptions)],
        [field(9, "都道府県", "select", undefined, prefectureOptions)],
        [field(10, "市区町村")],
        [field(11, "町名・番地")],
        [field(12, "建物名・部屋番号")],
      ],
    },
    {
      title: "緊急連絡先",
      rows: [
        [field(13, "姓"), field(14, "名")],
        [field(15, "姓（カナ）"), field(16, "名（カナ）")],
        [field(17, "続柄"), field(18, "電話番号", "input", "tel")],
      ],
    },
  ],
  postalCompletions: [
    {
      postalIndex: 7,
      values: {
        8: "日本",
        9: "東京都",
        10: "千代田区",
        11: "千代田1-1",
      },
    },
  ],
};

const purposeDefinition: FormDefinition = {
  title: "配送・請求・お問い合わせ情報",
  description: "注文と連絡に必要な情報を入力してください。",
  groups: [
    {
      title: "配送先",
      rows: [
        [field(1, "姓"), field(2, "名")],
        [field(3, "姓（カナ）"), field(4, "名（カナ）")],
        [field(5, "電話番号", "input", "tel")],
        [field(6, "郵便番号", "input", "numeric")],
        [field(7, "国", "select", undefined, countryOptions)],
        [field(8, "都道府県", "select", undefined, prefectureOptions)],
        [field(9, "市区町村")],
        [field(10, "町名・番地")],
        [field(11, "建物名・部屋番号")],
        [field(12, "配送時の連絡事項", "textarea")],
      ],
    },
    {
      title: "請求先",
      rows: [
        [field(13, "法人名"), field(14, "部署名")],
        [field(15, "宛名")],
        [field(16, "郵便番号", "input", "numeric")],
        [field(17, "国", "select", undefined, countryOptions)],
        [field(18, "都道府県", "select", undefined, prefectureOptions)],
        [field(19, "市区町村")],
        [field(20, "町名・番地")],
        [field(21, "建物名・部屋番号")],
        [field(22, "電話番号", "input", "tel")],
        [field(23, "メールアドレス", "input", "email")],
      ],
    },
    {
      title: "お問い合わせ先",
      rows: [
        [field(24, "姓"), field(25, "名")],
        [field(26, "姓（カナ）"), field(27, "名（カナ）")],
        [field(28, "メールアドレス", "input", "email")],
        [field(29, "電話番号", "input", "tel")],
      ],
    },
  ],
  postalCompletions: [
    {
      postalIndex: 6,
      values: {
        7: "日本",
        8: "東京都",
        9: "千代田区",
        10: "千代田1-1",
      },
    },
    {
      postalIndex: 16,
      values: {
        17: "日本",
        18: "東京都",
        19: "千代田区",
        20: "千代田1-1",
      },
    },
  ],
};

function field(
  index: number,
  label: string,
  kind: FieldKind = "input",
  inputMode: FieldDefinition["inputMode"] = "text",
  options?: readonly string[],
): FieldDefinition {
  return { index, label, kind, inputMode, options };
}

function updateControlValue(
  form: HTMLFormElement,
  index: number,
  value: string,
) {
  const control = form.elements.namedItem(`field-${index}`);
  if (
    !(
      control instanceof HTMLInputElement ||
      control instanceof HTMLSelectElement ||
      control instanceof HTMLTextAreaElement
    )
  ) {
    return;
  }
  control.value = value;
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function FieldControl({
  definition,
  completion,
}: {
  definition: FieldDefinition;
  completion?: PostalCompletion;
}) {
  const id = `control-${definition.index}`;
  const name = `field-${definition.index}`;
  const shared = {
    id,
    name,
    className: "fixture-control",
  };
  const handleBlur = completion
    ? (event: FocusEvent<HTMLInputElement>) => {
        if (event.currentTarget.value.replace(/[^0-9]/g, "") !== "1000001") {
          return;
        }
        const form = event.currentTarget.form;
        if (!form) {
          return;
        }
        for (const [index, value] of Object.entries(completion.values)) {
          updateControlValue(form, Number(index), value);
        }
      }
    : undefined;

  return (
    <div className="grid gap-2 fixture-field">
      <label className="fixture-label" htmlFor={id}>
        {definition.label}
      </label>
      {definition.kind === "select" ? (
        <select {...shared} defaultValue="">
          {definition.options?.map((option) => (
            <option key={option || "empty"} value={option}>
              {option || "選択してください"}
            </option>
          ))}
        </select>
      ) : definition.kind === "textarea" ? (
        <textarea {...shared} rows={3} />
      ) : (
        <input
          {...shared}
          inputMode={definition.inputMode}
          onBlur={handleBlur}
        />
      )}
    </div>
  );
}

function FieldRows({
  group,
  postalCompletions,
}: {
  group: FieldGroup;
  postalCompletions: readonly PostalCompletion[];
}) {
  return group.rows.map((row) => (
    <div
      className={`grid gap-4 fixture-row ${
        row.length === 2 ? "grid-cols-2" : "grid-cols-1"
      }`}
      key={row.map((definition) => definition.index).join("-")}
    >
      {row.map((definition) => (
        <FieldControl
          definition={definition}
          completion={postalCompletions.find(
            (candidate) => candidate.postalIndex === definition.index,
          )}
          key={definition.index}
        />
      ))}
    </div>
  ));
}

function SemanticGroups({ definition }: { definition: FormDefinition }) {
  return definition.groups.map((group) => (
    <fieldset className="contents fixture-group" key={group.title}>
      <legend className="contents fixture-group-legend">
        <span className="fixture-group-title">{group.title}</span>
      </legend>
      <FieldRows
        group={group}
        postalCompletions={definition.postalCompletions}
      />
    </fieldset>
  ));
}

function PresentationGroups({ definition }: { definition: FormDefinition }) {
  return definition.groups.map((group) => (
    <Fragment key={group.title}>
      <h2 className="contents fixture-group-heading">
        <span className="fixture-group-title">{group.title}</span>
      </h2>
      <FieldRows
        group={group}
        postalCompletions={definition.postalCompletions}
      />
    </Fragment>
  ));
}

export function FormGroupMembershipFixture({
  context,
  experimentId = "form-group-membership-reconstruction",
  fixtureId,
  structure,
}: FormGroupMembershipFixtureProps) {
  const definition =
    context === "person" ? personDefinition : purposeDefinition;
  const [submittedEntries, setSubmittedEntries] = useState<
    readonly (readonly [string, string])[] | null
  >(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const entries = [...new FormData(event.currentTarget).entries()].map(
      ([name, value]) => [name, String(value)] as const,
    );
    setSubmittedEntries(entries);
  }

  return (
    <main
      lang="ja"
      className="form-group-membership-fixture"
      data-fixture-root
      data-experiment-id={experimentId}
      data-fixture-id={fixtureId}
      data-fixture-version="v1"
    >
      <form
        className="grid gap-4 fixture-form"
        method="post"
        onSubmit={handleSubmit}
      >
        <header className="grid gap-2 fixture-heading">
          <p className="fixture-eyebrow">KOKOCHI UI</p>
          <h1>{definition.title}</h1>
          <p>{definition.description}</p>
        </header>
        <input type="hidden" name="metadata-1" value="fixture-v1" />
        <input type="hidden" name="metadata-2" value="local-only" />
        {structure === "semantic-first" ? (
          <SemanticGroups definition={definition} />
        ) : (
          <PresentationGroups definition={definition} />
        )}
        <div className="fixture-actions">
          <button className="fixture-submit" type="submit">
            入力内容を確認する
          </button>
        </div>
        {submittedEntries ? (
          <output
            aria-live="polite"
            className="fixture-confirmation"
            data-submission-summary={JSON.stringify(submittedEntries)}
          >
            入力内容をローカルで確認しました。
          </output>
        ) : null}
      </form>
    </main>
  );
}
