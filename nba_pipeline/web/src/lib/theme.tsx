import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

export type ThemeId = 'tipoff' | 'calm' | 'dusk' | 'classic';
export type Density = 'comfortable' | 'compact';

export interface ThemeOption {
  id: ThemeId;
  label: string;
  description: string;
}

/**
 * `tipoff` is the default brand palette sampled from the Basketball Data Science mark.
 * `calm` keeps the warm paper tones; `classic` is the original high-contrast explorer blue.
 */
export const THEMES: ThemeOption[] = [
  { id: 'tipoff', label: 'Tip-off', description: 'Cyan, royal blue, and orange from the Basketball Data Science mark' },
  { id: 'calm', label: 'Calm paper', description: 'Warm, low-glare light theme for long sessions' },
  { id: 'dusk', label: 'Dusk', description: 'Dimmed dark theme with muted accents' },
  { id: 'classic', label: 'Classic', description: 'The original high-contrast blue palette' },
];

const THEME_IDS = THEMES.map(option => option.id) as ThemeId[];
const THEME_KEY = 'nba-explorer-theme-v2';
const DENSITY_KEY = 'nba-explorer-density';

interface ThemeContextValue {
  theme: ThemeId;
  density: Density;
  setTheme: (theme: ThemeId) => void;
  setDensity: (density: Density) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  if (typeof localStorage === 'undefined') return fallback;
  const value = localStorage.getItem(key) as T | null;
  return value && allowed.includes(value) ? value : fallback;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => stored(THEME_KEY, THEME_IDS, 'tipoff'));
  const [density, setDensityState] = useState<Density>(() => stored(DENSITY_KEY, ['comfortable', 'compact'] as const, 'comfortable'));

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.density = density;
  }, [theme, density]);

  const setTheme = useCallback((next: ThemeId) => {
    setThemeState(next);
    localStorage.setItem(THEME_KEY, next);
  }, []);

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    localStorage.setItem(DENSITY_KEY, next);
  }, []);

  const value = useMemo(() => ({ theme, density, setTheme, setDensity }), [theme, density, setTheme, setDensity]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used inside ThemeProvider');
  return context;
}
