// B/H source admission. Conversion has a separate, narrower policy.
export const excludedSource = path => /^(?:40-输出|50-写作范本|90-Logs|20-Wiki|30-Projects|99-System|00-Test)\//.test(path) || path.startsWith('10-Sources/程序整理记录/');
export const privateInbox = path => /^00-Inbox\/(?:随手放|日记)\//.test(path);
export const diary = path => path.startsWith('00-Inbox/日记/');
export const frontmatter = text => text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1]??'';
export const nonSource = text => /^kind:\s*["']?(?:guide|status|test|generated|原件登记)["']?\s*$/m.test(frontmatter(text));
export function diaryStable(state,snapshot,now){
 state.diary_observations??={};
 let observed=state.diary_observations[snapshot.path];
 if(!observed||observed.sha256!==snapshot.sha256){observed={sha256:snapshot.sha256,first_seen:now};state.diary_observations[snapshot.path]=observed;}
 return Date.parse(now)-Math.max(Date.parse(observed.first_seen),Date.parse(snapshot.mtime))>=6*3600_000;
}

// Owner-maintained glossary: automatic ingest/repair never modifies it.
export const automaticReadonly = path => path === "20-Wiki/综合/公司术语表.md";
