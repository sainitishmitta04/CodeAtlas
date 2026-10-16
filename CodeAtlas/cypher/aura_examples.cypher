// CodeAtlas — queries for Neo4j Aura Query / Browser (graph view).
// 1) Run query 0 to find your project id, 2) set it:  :param pid => '<project id>'
// Paths below refer to the fixture repo (CodeAtlas/tests/fixtures/sample_repo);
// replace them with paths from your own project.

// 0. CodeAtlas projects (table)
MATCH (p:Project {source: 'codeatlas'})
RETURN p.id AS id, p.name AS name, p.sync_status AS status, p.file_count AS files, p.synced_at AS synced;

// 1. Project -> files
MATCH (p:Project {id: $pid})-[r:CONTAINS]->(f:File)
RETURN p, r, f LIMIT 50;

// 2. Files -> classes / functions, plus class methods
MATCH (f:File {project_id: $pid})-[d:DEFINES]->(s)
OPTIONAL MATCH (s)-[m:HAS_METHOD]->(fn:Function)
RETURN f, d, s, m, fn LIMIT 150;

// 3. Import graph (project files and external/unresolved modules)
MATCH (a:File {project_id: $pid})-[i:IMPORTS]->(b)
RETURN a, i, b LIMIT 150;

// 3b. Internal file-to-file dependencies only
MATCH (a:File {project_id: $pid})-[i:IMPORTS]->(b:File)
RETURN a, i, b LIMIT 150;

// 4. Multi-hop dependency path (shortest IMPORTS chain)
MATCH (a:File {project_id: $pid, path: 'shop/cli.py'})
MATCH (b:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = shortestPath((a)-[:IMPORTS*1..6]->(b))
RETURN p;

// 4b. Everything a file depends on, up to 3 hops
MATCH p = (f:File {project_id: $pid, path: 'shop/cli.py'})-[:IMPORTS*1..3]->(:File)
RETURN p LIMIT 50;

// 4c. Call chains reaching a function (edges carry lines / confidence evidence)
MATCH p = (:Function {project_id: $pid})-[:CALLS*1..3]->(fn:Function {project_id: $pid, name: 'fmt'})
RETURN p LIMIT 50;

// 5. A selected component and its connected neighbourhood (2 hops, bounded)
MATCH (c:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (c)-[*1..2]-(x)
WHERE x.project_id = $pid
RETURN p LIMIT 80;

// 5b. Change impact of a file: importers (file level) and callers of its functions (function level)
MATCH (f:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (:File)-[:IMPORTS*1..2]->(f)
RETURN p
UNION
MATCH (f:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (:Function)-[:CALLS*1..2]->(:Function)<-[:DEFINES]-(f)
RETURN p;

// 6. Node counts by type (table)
MATCH (n:File {project_id: $pid}) RETURN 'File' AS label, count(n) AS count
UNION ALL MATCH (n:Module {project_id: $pid}) RETURN 'Module' AS label, count(n) AS count
UNION ALL MATCH (n:Class {project_id: $pid}) RETURN 'Class' AS label, count(n) AS count
UNION ALL MATCH (n:Function {project_id: $pid}) RETURN 'Function' AS label, count(n) AS count;

// 6b. Relationship counts by type (table)
MATCH (:Project {id: $pid})-[r:CONTAINS]->() RETURN 'CONTAINS' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:DEFINES]->() RETURN 'DEFINES' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:HAS_METHOD]->() RETURN 'HAS_METHOD' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:IMPORTS]->() RETURN 'IMPORTS' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:INHERITS]->() RETURN 'INHERITS' AS type, count(r) AS count
UNION ALL MATCH (:Function {project_id: $pid})-[r:CALLS]->() RETURN 'CALLS' AS type, count(r) AS count;

// 6c. Call evidence quality (table)
MATCH (:Function {project_id: $pid})-[r:CALLS]->(:Function)
RETURN r.confidence AS confidence, count(r) AS edges, sum(r.count) AS call_sites;
