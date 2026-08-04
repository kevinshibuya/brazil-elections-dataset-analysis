import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router";

import Layout from "./components/Layout";
import { createQueryClient } from "./data/hooks";
import { I18nProvider } from "./i18n";
import Detectors from "./pages/Detectors";
import Methodology from "./pages/Methodology";
import Municipality from "./pages/Municipality";
import NotFound from "./pages/NotFound";
import Overview from "./pages/Overview";

/**
 * Providers + routes. Everything the app needs is client-side and static:
 * React Query is the fetch/cache layer over the immutable export, the router
 * is history-based (the deploy slice adds the SPA fallback), and the locale
 * provider wraps both so every string can go through `t()`.
 */
export default function App() {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Overview />} />
              <Route path="detectors" element={<Detectors />} />
              <Route path="municipality/:code" element={<Municipality />} />
              <Route path="methodology" element={<Methodology />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
