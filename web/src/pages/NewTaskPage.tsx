import { useLocation } from "react-router-dom";
import { Composer, type ComposerPreset } from "../components/Composer";

export function NewTaskPage() {
  const location = useLocation();
  const preset = (location.state as ComposerPreset | null) ?? undefined;
  // Keyed on the navigation entry so "Reuse instruction" gives a fresh, prefilled form.
  return <Composer key={location.key} preset={preset} />;
}
