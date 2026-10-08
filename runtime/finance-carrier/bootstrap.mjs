#!/usr/bin/env node
/** Private finance-only carrier; preserve the upstream dsh profile launcher. */
const selectors = ['DSH_SUBPROCESS_RUNNER', 'DSH_PTC_RUNTIME_NODE'];
if (selectors.some(name => process.env[name] !== undefined)) {
  throw new Error('MingJing finance carrier does not expose subprocess or code runtimes');
}
const argv = process.argv.slice(2);
if (argv.length !== 2 || argv[0] !== '--profile' || argv[1] !== 'mingjing') {
  throw new Error('MingJing finance carrier accepts only --profile mingjing');
}
if (!process.env.DSH_HOME) throw new Error('MingJing finance carrier requires its owned DSH_HOME');
// No Office resolver, ACL runner or arbitrary profile bootstrap is required by
// the financial profile. The actual loop and profile boot remain upstream code.
const { runCli } = await import('@deepseek-ai/dsh/lib/bin.js');
await runCli();
