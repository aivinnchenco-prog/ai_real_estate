/**
 * generate_booking_request.js — Агент 8 (Notary)
 * Генерирует двуязычную (EN/RU) БРОНЬ-ЗАЯВКУ (соглашение о резервировании) .docx.
 *
 * ВАЖНО ПО СУТИ: это НЕ договор оплаты. Оплата и условия проживания
 * оформляются отдельным договором после показа объекта и полного
 * согласования с владельцем. Этот документ фиксирует:
 *   - параметры запроса клиента (даты, гости, бюджет, питомцы)
 *   - объект, по которому идёт бронь
 *   - порядок дальнейших шагов (показ → согласование → основной договор)
 *
 * Использование:
 *   node generate_booking_request.js booking_data.json out.docx
 */

const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, AlignmentType, BorderStyle,
} = require("docx");

const [,, dataPath, outPath] = process.argv;
if (!dataPath || !outPath) {
  console.error("usage: node generate_booking_request.js booking_data.json out.docx");
  process.exit(1);
}
const D = JSON.parse(fs.readFileSync(dataPath, "utf-8"));

const GOLD = "B8962E";
const INK = "1a1a1a";
const DIM = "555555";

const t = (text, opts = {}) => new TextRun({ text, font: "Georgia", size: 19, color: INK, ...opts });
const b = (text, opts = {}) => t(text, { bold: true, ...opts });
const p = (children, opts = {}) =>
  new Paragraph({ children: Array.isArray(children) ? children : [children], spacing: { after: 80 }, ...opts });

const noBorders = {
  top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE },
  left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE },
  insideHorizontal: { style: BorderStyle.NONE }, insideVertical: { style: BorderStyle.NONE },
};

const COL = 4680;
function biRow(enChildren, ruChildren) {
  return new TableRow({
    children: [
      new TableCell({ width: { size: COL, type: WidthType.DXA },
        margins: { top: 40, bottom: 40, left: 0, right: 160 }, children: enChildren }),
      new TableCell({ width: { size: COL, type: WidthType.DXA },
        margins: { top: 40, bottom: 40, left: 160, right: 0 }, children: ruChildren }),
    ],
  });
}
const biTable = rows => new Table({
  width: { size: 9360, type: WidthType.DXA }, columnWidths: [COL, COL],
  borders: noBorders, rows,
});
const H = (en, ru) => biRow(
  [p(b(en, { size: 20 }), { spacing: { before: 160, after: 60 } })],
  [p(b(ru, { size: 20 }), { spacing: { before: 160, after: 60 } })]
);
const T = (en, ru) => biRow([p(t(en))], [p(t(ru))]);

const rows = [];

// Шапка
rows.push(biRow(
  [p(b("RESERVATION REQUEST AGREEMENT", { size: 22 }), { alignment: AlignmentType.CENTER })],
  [p(b("СОГЛАШЕНИЕ О БРОНИРОВАНИИ (ЗАЯВКА)", { size: 22 }), { alignment: AlignmentType.CENTER })],
));
rows.push(biRow(
  [p(t(`No. ${D.contract.number} · ${D.contract.date}`, { color: DIM }), { alignment: AlignmentType.CENTER })],
  [p(t(`№ ${D.contract.number} · ${D.contract.date}`, { color: DIM }), { alignment: AlignmentType.CENTER })],
));

// 1. Стороны
rows.push(H("1. Parties", "1. Стороны"));
rows.push(T(
  `1.1. Agency: ${D.agency.name}. E-mail: ${D.agency.email}, phone/WhatsApp: ${D.agency.phone}.`,
  `1.1. Агентство: ${D.agency.name}. E-mail: ${D.agency.email}, телефон/WhatsApp: ${D.agency.phone}.`
));
rows.push(T(
  `1.2. Client: ${D.client.fullName}, citizenship: ${D.client.citizenship}. Phone/messenger: ${D.client.phone}${D.client.email ? ", e-mail: " + D.client.email : ""}.`,
  `1.2. Клиент: ${D.client.fullName}, гражданство: ${D.client.citizenship}. Телефон/мессенджер: ${D.client.phone}${D.client.email ? ", e-mail: " + D.client.email : ""}.`
));

// 2. Объект
rows.push(H("2. Property", "2. Объект бронирования"));
rows.push(T(
  `Property: ${D.object.propertyName}. Address: ${D.object.address}. Unit/Villa: ${D.object.unit}. Bedrooms: ${D.object.bedrooms}. Area: ${D.object.area} sq.m. Object ID: ${D.object.objectId}.`,
  `Объект: ${D.object.propertyName}. Адрес: ${D.object.address}. Апартаменты/вилла: ${D.object.unit}. Спален: ${D.object.bedrooms}. Площадь: ${D.object.area} кв.м. ID объекта: ${D.object.objectId}.`
));

// 3. Параметры запроса клиента
rows.push(H("3. Client's Request Parameters", "3. Параметры запроса Клиента"));
rows.push(T(
  `3.1. Period of stay: from ${D.request.checkinDate} to ${D.request.checkoutDate}${D.request.longTerm ? " (long-term / " + D.request.longTermNote + ")" : ""}.`,
  `3.1. Период проживания: с ${D.request.checkinDate} по ${D.request.checkoutDate}${D.request.longTerm ? " (долгосрочно / " + D.request.longTermNoteRu + ")" : ""}.`
));
rows.push(T(
  `3.2. Number of guests: ${D.request.guests}.`,
  `3.2. Количество проживающих: ${D.request.guests}.`
));
rows.push(T(
  `3.3. Client's budget: ${D.request.budget} ${D.request.budgetCurrency} ${D.request.budgetPeriod}. Final price is subject to confirmation by the property owner and will be fixed in the main agreement (Section 5).`,
  `3.3. Бюджет Клиента: ${D.request.budget} ${D.request.budgetCurrency} ${D.request.budgetPeriodRu}. Итоговая цена подтверждается владельцем объекта и фиксируется в основном договоре (раздел 5).`
));
rows.push(T(
  `3.4. Pets: ${D.request.pets ? "yes — " + (D.request.petsNote || "details agreed separately") : "no"}.`,
  `3.4. Питомцы: ${D.request.pets ? "да — " + (D.request.petsNoteRu || "детали согласовываются отдельно") : "нет"}.`
));

// 4. Что фиксирует это соглашение
rows.push(H("4. Effect of this Reservation", "4. Что закрепляет данное бронирование"));
rows.push(T(
  `4.1. The Agency reserves the Property for the Client for the requested period and undertakes to arrange a viewing (in person or online) and to confirm availability and final terms with the property owner.`,
  `4.1. Агентство резервирует Объект за Клиентом на запрошенный период и обязуется организовать показ (очный или онлайн), а также подтвердить у владельца доступность и итоговые условия.`
));
rows.push(T(
  `4.2. This reservation is free of charge and does not oblige the Client to pay any amounts. No payment is made under this document.`,
  `4.2. Бронирование по данному документу бесплатно и не обязывает Клиента к оплате. Никакие платежи по настоящему документу не производятся.`
));
rows.push(T(
  `4.3. The reservation is valid until ${D.contract.validUntil}. If the main agreement is not concluded by this date, the reservation is released automatically without any obligations of the parties.`,
  `4.3. Бронь действует до ${D.contract.validUntil}. Если до этой даты основной договор не заключён, бронь снимается автоматически без обязательств сторон.`
));

// 5. Дальнейшие шаги
rows.push(H("5. Next Steps and Main Agreement", "5. Дальнейшие шаги и основной договор"));
rows.push(T(
  `5.1. After the viewing and full confirmation of terms with the property owner, the parties conclude a separate agreement on temporary residence, which will set out the final price, payment procedure, deposits and house rules.`,
  `5.1. После показа Объекта и полного согласования условий с владельцем стороны заключают отдельный договор о временном проживании, в котором фиксируются итоговая цена, порядок оплаты, депозиты и правила проживания.`
));
rows.push(T(
  `5.2. Until the main agreement is concluded, neither party bears financial obligations towards the other under this document.`,
  `5.2. До заключения основного договора стороны не несут финансовых обязательств друг перед другом по настоящему документу.`
));

// 6. Согласие и связь
rows.push(H("6. Communication and Consent", "6. Связь и согласие"));
rows.push(T(
  `6.1. This document may be sent electronically (e-mail, messengers). The Client's confirmation in a messenger ("confirm", "согласен" or similar) is deemed acceptance of this reservation.`,
  `6.1. Документ может направляться электронно (e-mail, мессенджеры). Подтверждение Клиента в мессенджере («подтверждаю», «согласен» и т.п.) считается акцептом данного бронирования.`
));
rows.push(T(
  `6.2. The Client consents to the processing of the personal data provided above for the purpose of arranging the viewing and preparing the main agreement.`,
  `6.2. Клиент даёт согласие на обработку указанных выше персональных данных в целях организации показа и подготовки основного договора.`
));

// Подписи / подтверждение
rows.push(H("7. Confirmation", "7. Подтверждение"));
rows.push(biRow(
  [
    p(t("Client / Клиент:"), { spacing: { before: 120 } }),
    p(t("____________________________"), { spacing: { before: 160 } }),
    p(t("(signature or messenger confirmation)", { color: DIM, size: 16 })),
  ],
  [
    p(t(`Agency / Агентство: ${D.agency.brand}`), { spacing: { before: 120 } }),
    p(t("____________________________"), { spacing: { before: 160 } }),
    p(t("(подпись или электронное подтверждение)", { color: DIM, size: 16 })),
  ]
));

const doc = new Document({
  styles: { default: { document: { run: { font: "Georgia", size: 19 } } } },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 },
      margin: { top: 1000, bottom: 1000, left: 1100, right: 1100 } } },
    children: [
      p([b(D.agency.brand, { size: 30, color: GOLD }), t("  ·  real estate", { size: 18, color: DIM })],
        { alignment: AlignmentType.CENTER, spacing: { after: 200 } }),
      biTable(rows),
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log("OK:", outPath);
});
