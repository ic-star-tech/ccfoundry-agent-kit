import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { currentStamp, dependencyEnvironments, dependencyFingerprint, dependencyOptions, writeStamp } from "../../scripts/dev-board-deps.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "kit deps "));
  t.after(() => rmSync(root, {recursive:true,force:true}));
  return root;
}

for (const args of [["--offline"], ["--wheelhouse"], ["--wheelhouse="], ["--npm-cache"], ["--offline", "--online"], ["--offline=false"]]) {
  test(`invalid setup options fail before installation: ${JSON.stringify(args)}`, () => {
    assert.throws(() => dependencyOptions(args, {}));
  });
}

test("offline selection honors CLI override and paths with spaces", t => {
  const root = fixture(t), wheels = join(root, "company wheels");mkdirSync(wheels);
  writeFileSync(join(wheels,"fixture.whl"), "fixture");
  const options = dependencyOptions(["--offline","--wheelhouse","company wheels","--npm-cache=private cache"],{},root);
  assert.equal(options.wheelhouse,wheels);assert.equal(options.npmCache,join(root,"private cache"));
  const env = dependencyEnvironments(options,{PIP_INDEX_URL:"https://private.invalid/simple",PIP_FIND_LINKS:"https://public.invalid/wheels",NPM_TOKEN:"private-token"});
  assert.equal(env.pip.PIP_NO_INDEX,"1");
  assert.ok(env.pip.PIP_FIND_LINKS.startsWith("file:"));assert.ok(env.pip.PIP_FIND_LINKS.includes("%20"));
  assert.ok(!env.pip.PIP_FIND_LINKS.includes("public.invalid"));
  assert.equal(env.npm.npm_config_offline,"true");
  assert.equal(dependencyOptions(["--online"],{CCFOUNDRY_OFFLINE:"1"},root).offline,false);
});

test("stamps notice changed content with preserved timestamps", t => {
  const root=fixture(t), path=join(root,"pyproject.toml"), stamp=join(root,"stamp");
  writeFileSync(path,'version = "1"'); const original=statSync(path);
  const input={kind:"python",packageRoot:root,watchedFiles:[path],metadata:{python:"fixture"},options:{offline:false,wheelhouse:"",npmCache:""},environment:{}};
  const before=dependencyFingerprint(input);writeStamp(stamp,before);
  writeFileSync(path,'version = "2"');utimesSync(path,original.atime,original.mtime);
  assert.equal(currentStamp(stamp,dependencyFingerprint(input)),false);
  assert.equal(statSync(stamp).mode & 0o777,0o600);
});

test("configuration and CA contents invalidate only the corresponding tool", t => {
  const root=fixture(t), cert=join(root,"private-ca.pem");writeFileSync(cert,"CA ONE");
  const base={packageRoot:root,watchedFiles:[],metadata:{configuration:"first",certificate:"CA ONE"},options:{offline:false,wheelhouse:"",npmCache:""},environment:{PIP_CERT:cert,npm_config_registry:"https://one.invalid"}};
  assert.notEqual(dependencyFingerprint({...base,kind:"python"}),dependencyFingerprint({...base,kind:"python",metadata:{...base.metadata,certificate:"CA TWO"}}));
  const changed={...base,environment:{...base.environment,npm_config_registry:"https://two.invalid"}};
  assert.equal(dependencyFingerprint({...base,kind:"python"}),dependencyFingerprint({...changed,kind:"python"}));
  assert.notEqual(dependencyFingerprint({...base,kind:"web"}),dependencyFingerprint({...changed,kind:"web"}));
});

test("stamp records only opaque fingerprints, never mirror credentials", t => {
  const root=fixture(t), stamp=join(root,"stamp");
  const hash=dependencyFingerprint({kind:"python",packageRoot:root,watchedFiles:[],metadata:{index:"https://user:super-private-password@registry.invalid"},environment:{PIP_INDEX_URL:"https://user:super-private-password@registry.invalid"},options:{offline:false,wheelhouse:"",npmCache:""}});
  writeStamp(stamp,hash);const text=readFileSync(stamp,"utf8");
  assert.deepEqual(Object.keys(JSON.parse(text)).sort(),["fingerprint","schema"]);
  assert.equal(text.includes("super-private-password"),false);assert.equal(text.includes("registry.invalid"),false);
  assert.equal(currentStamp(stamp,hash),true);
  writeFileSync(stamp,"partial JSON");assert.equal(currentStamp(stamp,hash),false);
});

test("moving the kit invalidates editable Python installations", t => {
  const root=fixture(t), other=join(root,"another install");mkdirSync(other);
  const base={kind:"python",packageRoot:root,watchedFiles:[],metadata:{},environment:{},options:{offline:false,wheelhouse:"",npmCache:""}};
  assert.notEqual(dependencyFingerprint(base),dependencyFingerprint({...base,packageRoot:other}));
});
