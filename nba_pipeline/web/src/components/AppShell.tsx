import { Link, NavLink, Route, Routes } from 'react-router-dom';
import mark from '../assets/mark.png';
import { Dashboard } from '../pages/Dashboard';
import { Players } from '../pages/Players';
import { PlayerDetail } from '../pages/PlayerDetail';
import { Shots } from '../pages/Shots';
import { ThemeMenu } from './ThemeMenu';

export function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header>
        <Link className="brand" to="/">
          <img className="brand-mark" src={mark} alt="" width={44} height={44} />
          <span className="brand-copy">
            <span className="brand-name">Basketball Data Science</span>
            <small>Local Explorer</small>
          </span>
        </Link>
        <nav aria-label="Main">
          <NavLink to="/players">Players</NavLink>
          <NavLink to="/shots">Shot Explorer</NavLink>
        </nav>
        <ThemeMenu />
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/players" element={<Players />} />
          <Route path="/players/:playerId" element={<PlayerDetail />} />
          <Route path="/shots" element={<Shots />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  );
}

function NotFound() {
  return (
    <>
      <h1>Page not found</h1>
      <p><Link to="/">Return to the dashboard</Link></p>
    </>
  );
}
