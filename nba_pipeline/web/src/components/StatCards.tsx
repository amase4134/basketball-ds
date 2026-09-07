
export interface StatCard {
  label: string;
  value: string;
  note?: string;
}

export function StatCards({ cards }: { cards: StatCard[] }) {
  return (
    <section className="cards">
      {cards.map(card => (
        <article key={card.label}>
          <small>{card.label}</small>
          <strong>{card.value}</strong>
          {card.note && <span className="card-note">{card.note}</span>}
        </article>
      ))}
    </section>
  );
}
