import { Link, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import { EmptyState } from "./components/ui";
import Home from "./pages/Home";
import Explorer from "./pages/Explorer";
import StoryDetail from "./pages/StoryDetail";
import Preserve from "./pages/Preserve";
import Digitizer from "./pages/Digitizer";
import ScriptExplorer from "./pages/ScriptExplorer";
import AudioLibrary from "./pages/AudioLibrary";
import HeritageMap from "./pages/HeritageMap";
import Timeline from "./pages/Timeline";
import Future from "./pages/Future";
import Dashboard from "./pages/Dashboard";
import { Login, Register } from "./pages/Auth";
import Settings from "./pages/Settings";
import BodhanHealth from "./pages/BodhanHealth";
import Try from "./pages/Try";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="explore" element={<Explorer />} />
        <Route path="stories/:id" element={<StoryDetail />} />
        <Route path="scripts" element={<ScriptExplorer />} />
        <Route path="map" element={<HeritageMap />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="try" element={<Try />} />
        <Route path="login" element={<Login />} />
        <Route path="register" element={<Register />} />
        <Route element={<ProtectedRoute />}>
          <Route path="preserve" element={<Preserve />} />
          <Route path="preserve/:id" element={<Preserve />} />
          <Route path="digitizer" element={<Digitizer />} />
          <Route path="audio" element={<AudioLibrary />} />
          <Route path="future" element={<Future />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="settings" element={<Settings />} />
          <Route path="admin/bodhan-health" element={<BodhanHealth />} />
          <Route path="bodhan-health" element={<BodhanHealth />} />
        </Route>
        <Route path="*" element={<div className="mx-auto max-w-xl px-4 py-20"><EmptyState title="Page not found" action={<Link to="/" className="btn-primary">Back to home</Link>}>That page does not exist.</EmptyState></div>} />
      </Route>
    </Routes>
  );
}
