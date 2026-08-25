function scc2_memory(input_dir, requested_threads, stage, rhs_count, output_file, source_commit, source_archive_sha256)
%SCC2_MEMORY Stop at an exact process-memory checkpoint for SCC2.
arguments
    input_dir (1,:) char
    requested_threads (1,1) double {mustBeInteger,mustBePositive}
    stage (1,:) char {mustBeMember(stage, {'baseline','input','graph','hierarchy','plan','workspace-one','workspace-pool','solve','batch'})}
    rhs_count (1,1) double {mustBeInteger,mustBePositive}
    output_file (1,:) char
    source_commit (1,:) char
    source_archive_sha256 (1,:) char
end

maxNumCompThreads(requested_threads);
started = tic;
checkpoints = checkpoint('baseline', started, 0);
family = getenv_default('CMG_FAMILY', 'unknown');
vertices = str2double(getenv_default('CMG_VERTICES', '0'));
edge_count = 0;
matrix_nonzeros = 0;
owned = struct('input', 0, 'graph', 0, 'hierarchy', 0, 'plan', 0, 'workspace_one', 0, 'workspace_pool', 0);
numerical = [];
warnings = {};
if strcmp(stage, 'baseline')
    write_result();
    return
end

[vertices, endpoints_u, endpoints_v, weights] = read_graph(fullfile(input_dir, 'graph.bin'));
right_hand_sides = read_vectors(fullfile(input_dir, 'rhs.bin'));
truths = read_vectors(fullfile(input_dir, 'truth.bin')); %#ok<NASGU>
metadata = jsondecode(fileread(fullfile(input_dir, 'metadata.json')));
family = metadata.family;
edge_count = numel(weights);
visible = whos('endpoints_u', 'endpoints_v', 'weights', 'right_hand_sides', 'truths');
owned.input = sum([visible.bytes]);
checkpoints(end + 1) = checkpoint('input', started, owned.input); %#ok<AGROW>
assert(rhs_count <= size(right_hand_sides, 2), 'Fixture has too few RHSs');
if strcmp(stage, 'input')
    write_result();
    return
end

u = double(endpoints_u) + 1;
v = double(endpoints_v) + 1;
diagonal = accumarray([u; v], [weights; weights], [vertices 1], @sum, 0);
A = sparse([u; v; (1:vertices)'], [v; u; (1:vertices)'], [-weights; -weights; diagonal], vertices, vertices);
matrix_nonzeros = nnz(A);
visible = whos('A');
owned.graph = sum([visible.bytes]);
checkpoints(end + 1) = checkpoint('graph', started, owned.graph); %#ok<AGROW>
if strcmp(stage, 'graph')
    write_result();
    return
end

opts = struct('validate', 0, 'matlab_', 0);
[pfun, H, hierarchy_flag] = cmg_precondition(A, opts);
visible = whos('H');
owned.hierarchy = sum([visible.bytes]);
checkpoints(end + 1) = checkpoint('hierarchy', started, owned.hierarchy); %#ok<AGROW>
if hierarchy_flag ~= 0
    warnings = {sprintf('Official CMG hierarchy flag %g', hierarchy_flag)};
end
if strcmp(stage, 'hierarchy')
    write_result();
    return
end

% The official workflow has no separately retained parallel plan.
warnings{end + 1} = 'parallel-plan stage unsupported by official MATLAB workflow';
checkpoints(end + 1) = checkpoint('plan', started, 0); %#ok<AGROW>
if strcmp(stage, 'plan')
    write_result();
    return
end

workspace_one = zeros(vertices, 1);
visible = whos('workspace_one');
owned.workspace_one = sum([visible.bytes]);
checkpoints(end + 1) = checkpoint('workspace-one', started, owned.workspace_one); %#ok<AGROW>
if strcmp(stage, 'workspace-one')
    write_result();
    return
end

workspace_pool = zeros(vertices, rhs_count); %#ok<NASGU>
visible = whos('workspace_pool');
owned.workspace_pool = sum([visible.bytes]);
checkpoints(end + 1) = checkpoint('workspace-pool', started, owned.workspace_pool); %#ok<AGROW>
if strcmp(stage, 'workspace-pool')
    write_result();
    return
end

if strcmp(stage, 'solve')
    [solution, flag, ~, iterations] = pcg(A, right_hand_sides(:, 1), 1e-8, 1000, pfun, [], workspace_one);
    residual = right_hand_sides(:, 1) - A * solution;
    operator_bound = 2 * max(full(diag(A)));
    backward = norm(residual) / max(norm(right_hand_sides(:, 1)) + operator_bound * norm(solution), realmin);
    numerical = struct('iterations', iterations, 'max_backward_error', backward, 'native_flag', flag);
    assert(flag == 0 && backward <= 1.1e-8, 'Memory-stage solve failed');
    checkpoints(end + 1) = checkpoint('solve', started, 0); %#ok<AGROW>
else
    max_backward = 0;
    total_iterations = 0;
    for rhs_index = 1:rhs_count
        [solution, flag, ~, iterations] = pcg(A, right_hand_sides(:, rhs_index), 1e-8, 1000, pfun, [], workspace_one);
        residual = right_hand_sides(:, rhs_index) - A * solution;
        operator_bound = 2 * max(full(diag(A)));
        backward = norm(residual) / max(norm(right_hand_sides(:, rhs_index)) + operator_bound * norm(solution), realmin);
        assert(flag == 0 && backward <= 1.1e-8, 'Memory-stage batch failed');
        max_backward = max(max_backward, backward);
        total_iterations = total_iterations + iterations;
    end
    numerical = struct('iterations', total_iterations, 'max_backward_error', max_backward, 'native_flag', 0);
    checkpoints(end + 1) = checkpoint('batch', started, 0); %#ok<AGROW>
end
write_result();

    function write_result()
        result = struct();
        result.protocol_version = 'cmg-scc2-v1';
        result.record_type = 'memory-stage';
        result.run_id = getenv_default('CMG_RUN_ID', 'local');
        result.task_id = str2double(getenv_default('CMG_TASK_ID', '1'));
        result.source_commit = source_commit;
        result.source_archive_sha256 = source_archive_sha256;
        result.binary_sha256 = getenv_default('CMG_MEX_BINARY_SHA256', repmat('0', 1, 64));
        result.environment_id = getenv_default('CMG_ENVIRONMENT_ID', repmat('0', 1, 64));
        result.implementation = 'matlab';
        result.family = family;
        result.vertices = vertices;
        result.canonical_edges = edge_count;
        result.matrix_nonzeros = matrix_nonzeros;
        result.threads = maxNumCompThreads;
        result.rhs_count = rhs_count;
        result.stage = stage;
        result.owned_bytes = owned;
        result.checkpoints = checkpoints;
        result.numerical = numerical;
        result.peak_rss_kb = peak_rss_kb();
        result.warnings = warnings;
        result.success = true;
        parent = fileparts(output_file);
        if ~isempty(parent) && ~isfolder(parent)
            mkdir(parent);
        end
        temporary = [output_file '.tmp'];
        fid = fopen(temporary, 'w');
        assert(fid >= 0, 'Could not open output file');
        cleanup = onCleanup(@() fclose(fid));
        fprintf(fid, '%s\n', jsonencode(result, PrettyPrint=true));
        clear cleanup
        movefile(temporary, output_file, 'f');
        fprintf('CMG_SCC2_MATLAB_MEMORY_SUCCESS stage=%s threads=%d\n', stage, result.threads);
    end
end

function value = checkpoint(stage, started, owned_bytes)
value = struct('stage', stage, 'elapsed_ns', round(toc(started) * 1e9), 'peak_rss_kb', peak_rss_kb(), 'owned_bytes', owned_bytes);
end

function value = getenv_default(name, fallback)
value = getenv(name);
if isempty(value)
    value = fallback;
end
end

function value = peak_rss_kb()
value = 0;
try
    pid = feature('getpid');
    text = fileread(sprintf('/proc/%d/status', pid));
    token = regexp(text, 'VmHWM:\s+(\d+)\s+kB', 'tokens', 'once');
    if ~isempty(token)
        value = str2double(token{1});
    end
catch
    value = 0;
end
end

function [vertices, u, v, weights] = read_graph(path)
fid = fopen(path, 'r', 'ieee-le');
assert(fid >= 0, 'Could not open graph input');
cleanup = onCleanup(@() fclose(fid));
magic = char(fread(fid, 8, 'uint8=>uint8')');
assert(strcmp(magic, 'CMGGRPH1'), 'Invalid graph magic');
vertices = double(fread(fid, 1, 'uint64=>uint64'));
edge_count = double(fread(fid, 1, 'uint64=>uint64'));
records = fread(fid, [3 edge_count], 'uint64=>uint64');
assert(size(records, 2) == edge_count, 'Truncated graph input');
u = records(1, :)';
v = records(2, :)';
weights = reshape(typecast(records(3, :), 'double'), [], 1);
end

function values = read_vectors(path)
fid = fopen(path, 'r', 'ieee-le');
assert(fid >= 0, 'Could not open vector input');
cleanup = onCleanup(@() fclose(fid));
magic = char(fread(fid, 8, 'uint8=>uint8')');
assert(strcmp(magic, 'CMGVEC01'), 'Invalid vector magic');
dimension = double(fread(fid, 1, 'uint64=>uint64'));
count = double(fread(fid, 1, 'uint64=>uint64'));
values = fread(fid, [dimension count], 'double=>double');
assert(isequal(size(values), [dimension count]), 'Truncated vector input');
end
