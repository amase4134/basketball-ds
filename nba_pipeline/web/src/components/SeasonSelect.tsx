
interface SeasonSelectProps {
  seasons: string[];
  value: string;
  onChange: (season: string) => void;
}

export function SeasonSelect({ seasons, value, onChange }: SeasonSelectProps) {
  return (
    <label>
      Season
      <select value={value} onChange={event => onChange(event.target.value)}>
        {seasons.map(season => <option key={season} value={season}>{season}</option>)}
      </select>
    </label>
  );
}
