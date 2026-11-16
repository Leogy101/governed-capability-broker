// This optional plugin must be explicitly installed and authorized by its operator.
// It never registers an agent tool, changes a session, or handles credential values.
import { createBridge } from "./bridge.mjs";

export default {
  id: "gcb-openclaw-host",
  name: "GCB host profiling",
  register(api) {
    const bridge = createBridge(api);
    api.registerGatewayMethod("gcb.profile.preflight", async ({ params, respond, signal }) => {
      respond(true, await bridge.preflight(params, signal), undefined);
    }, { scope: "operator.admin" });
    api.registerGatewayMethod("gcb.profile.complete", async ({ params, respond, signal }) => {
      respond(true, await bridge.complete(params, signal), undefined);
    }, { scope: "operator.admin" });
    api.registerGatewayMethod("gcb.profile.cancel", async ({ params, respond }) => {
      respond(true, bridge.cancel(params), undefined);
    }, { scope: "operator.admin" });
  },
};
