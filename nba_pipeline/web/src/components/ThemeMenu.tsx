import { THEMES, useTheme } from '../lib/theme';
import { usePopover } from '../lib/usePopover';

export function ThemeMenu() {
  const { theme, density, setTheme, setDensity } = useTheme();
  const { open, setOpen, ref } = usePopover();
  const current = THEMES.find(option => option.id === theme);

  return (
    <div className="theme-menu" ref={ref}>
      <button type="button" className="theme-trigger" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">◐</span> {current?.label ?? 'Theme'}
      </button>
      {open && (
        <div className="popover popover-wide popover-right" role="dialog" aria-label="Appearance settings">
          <p className="popover-title">Appearance</p>
          <fieldset>
            <legend className="visually-hidden">Theme</legend>
            {THEMES.map(option => (
              <label key={option.id} className="theme-option">
                <input type="radio" name="theme" checked={theme === option.id} onChange={() => setTheme(option.id)} />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend className="popover-title">Table density</legend>
            {(['comfortable', 'compact'] as const).map(option => (
              <label key={option} className="radio-label">
                <input type="radio" name="density" checked={density === option} onChange={() => setDensity(option)} />
                {option === 'comfortable' ? 'Comfortable' : 'Compact'}
              </label>
            ))}
          </fieldset>
        </div>
      )}
    </div>
  );
}
