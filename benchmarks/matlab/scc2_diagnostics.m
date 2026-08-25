function scc2_diagnostics(input_dir, requested_threads, repetitions, warmups, rhs_count, tolerance, strategy, output_file, source_commit, source_archive_sha256)
%SCC2_DIAGNOSTICS Official CMG/MEX diagnostic run on a canonical fixture.
arguments
    input_dir (1,:) char
    requested_threads (1,1) double {mustBeInteger,mustBePositive}
    repetitions (1,1) double {mustBeInteger,mustBePositive}
    warmups (1,1) double {mustBeInteger,mustBeNonnegative}
    rhs_count (1,1) double {mustBeInteger,mustBePositive}
    tolerance (1,1) double {mustBePositive}
    strategy (1,:) char {mustBeMember(strategy, {'native-sequential'})}
    output_file (1,:) char
    source_commit (1,:) char
    source_archive_sha256 (1,:) char
end

protocol_version = 'cmg-scc2-v1';
upstream_commit = '19752fc102f8cae8e34f66457bfaccb1aaa60375';
maxNumCompThreads(requested_threads);
applied_threads = maxNumCompThreads;

load_clock = tic;
[vertices, endpoints_u, endpoints_v, weights] = read_graph(fullfile(input_dir, 'graph.bin'));
right_hand_sides = read_vectors(fullfile(input_dir, 'rhs.bin'));
truths = read_vectors(fullfile(input_dir, 'truth.bin'));
metadata = jsondecode(fileread(fullfile(input_dir, 'metadata.json')));
input_load_ns = seconds_to_ns(toc(load_clock));
assert(rhs_count <= size(right_hand_sides, 2) && rhs_count <= size(truths, 2), 'Fixture has too few RHSs');
right_hand_sides = right_hand_sides(:, 1:rhs_count);
truths = truths(:, 1:rhs_count);

assembly_clock = tic;
u = double(endpoints_u) + 1;
v = double(endpoints_v) + 1;
diagonal = accumarray([u; v], [weights; weights], [vertices 1], @sum, 0);
A = sparse([u; v; (1:vertices)'], [v; u; (1:vertices)'], [-weights; -weights; diagonal], vertices, vertices);
graph_assembly_ns = seconds_to_ns(toc(assembly_clock));
clear u v diagonal endpoints_u endpoints_v
opts = struct('validate', 0, 'matlab_', 0);

for warmup = 1:warmups
    warm_pfun = cmg_precondition(A, opts);
    for rhs_index = 1:rhs_count
        [~, flag] = pcg(A, right_hand_sides(:, rhs_index), tolerance, 1000, warm_pfun, [], zeros(vertices, 1));
        assert(flag == 0, 'Warm-up PCG failed');
    end
end

samples = empty_samples();
order_position = 0;
final_solutions = zeros(vertices, rhs_count);
final_flags = zeros(1, rhs_count);
final_relres = zeros(1, rhs_count);
final_iterations = zeros(1, rhs_count);
pfun = [];
H = [];
hierarchy_flag = NaN;
for repetition = 1:repetitions
    total_wall = 0;
    total_cpu = 0;
    started = utc_now();
    wall_clock = tic;
    cpu_clock = cputime;
    [pfun, H, hierarchy_flag] = cmg_precondition(A, opts);
    wall_ns = seconds_to_ns(toc(wall_clock));
    cpu_ns = seconds_to_ns(cputime - cpu_clock);
    order_position = order_position + 1;
    samples(end + 1) = sample(repetition, order_position, started, 'preconditioner_setup', wall_ns, cpu_ns); %#ok<AGROW>
    total_wall = total_wall + wall_ns;
    total_cpu = total_cpu + cpu_ns;

    started = utc_now();
    wall_clock = tic;
    cpu_clock = cputime;
    initial_guess = zeros(vertices, 1); %#ok<NASGU>
    wall_ns = seconds_to_ns(toc(wall_clock));
    cpu_ns = seconds_to_ns(cputime - cpu_clock);
    order_position = order_position + 1;
    samples(end + 1) = sample(repetition, order_position, started, 'workspace_allocation', wall_ns, cpu_ns); %#ok<AGROW>
    total_wall = total_wall + wall_ns;
    total_cpu = total_cpu + cpu_ns;

    started = utc_now();
    wall_clock = tic;
    cpu_clock = cputime;
    for rhs_index = 1:rhs_count
        [candidate, flag, relres, iterations] = pcg(A, right_hand_sides(:, rhs_index), tolerance, 1000, pfun, [], zeros(vertices, 1));
        final_solutions(:, rhs_index) = candidate;
        final_flags(rhs_index) = flag;
        final_relres(rhs_index) = relres;
        final_iterations(rhs_index) = iterations;
    end
    wall_ns = seconds_to_ns(toc(wall_clock));
    cpu_ns = seconds_to_ns(cputime - cpu_clock);
    order_position = order_position + 1;
    samples(end + 1) = sample(repetition, order_position, started, 'pcg_solve', wall_ns, cpu_ns); %#ok<AGROW>
    total_wall = total_wall + wall_ns;
    total_cpu = total_cpu + cpu_ns;
    order_position = order_position + 1;
    samples(end + 1) = sample(repetition, order_position, started, 'solver_total', total_wall, total_cpu); %#ok<AGROW>
end
assert(~isempty(pfun), 'CMG did not produce a preconditioner');

apply_loops = 0;
if rhs_count == 1
    apply_loops = calibrate_apply(pfun, right_hand_sides(:, 1));
    for repetition = 1:repetitions
        started = utc_now();
        wall_clock = tic;
        cpu_clock = cputime;
        for loop = 1:apply_loops
            apply_output = pfun(right_hand_sides(:, 1)); %#ok<NASGU>
        end
        wall_ns = seconds_to_ns(toc(wall_clock)) / apply_loops;
        cpu_ns = seconds_to_ns(cputime - cpu_clock) / apply_loops;
        order_position = order_position + 1;
        samples(end + 1) = sample(repetition, order_position, started, 'preconditioner_apply', wall_ns, cpu_ns); %#ok<AGROW>
    end
end

operator_bound = 2 * max(full(diag(A)));
all_rhs = repmat(struct('iterations', 0, 'restarts', 0, 'native_flag', 0, 'native_relative_residual', 0, 'independent_relative_residual', 0, 'backward_error', 0, 'reference_scaled_error', 0, 'energy_norm_error', 0), 1, rhs_count);
for rhs_index = 1:rhs_count
    solution = final_solutions(:, rhs_index);
    rhs = right_hand_sides(:, rhs_index);
    truth = truths(:, rhs_index);
    residual = rhs - A * solution;
    residual_norm = norm(residual);
    rhs_norm = norm(rhs);
    error = (solution - mean(solution)) - (truth - mean(truth));
    truth_energy = max(real(truth' * (A * truth)), 0);
    error_energy = max(real(error' * (A * error)), 0);
    all_rhs(rhs_index).iterations = final_iterations(rhs_index);
    all_rhs(rhs_index).restarts = 0;
    all_rhs(rhs_index).native_flag = final_flags(rhs_index);
    all_rhs(rhs_index).native_relative_residual = final_relres(rhs_index);
    all_rhs(rhs_index).independent_relative_residual = residual_norm / max(rhs_norm, realmin);
    all_rhs(rhs_index).backward_error = residual_norm / max(rhs_norm + operator_bound * norm(solution), realmin);
    all_rhs(rhs_index).reference_scaled_error = centered_scaled_difference(solution, truth);
    all_rhs(rhs_index).energy_norm_error = sqrt(error_energy) / max(sqrt(truth_energy), realmin);
end
[level_vertices, level_nonzeros, repeat_counts] = hierarchy_metadata(H);
visible = whos('A', 'H', 'right_hand_sides', 'truths', 'final_solutions');
visible_bytes = sum([visible.bytes]);
warnings = {};
if hierarchy_flag ~= 0
    warnings = {sprintf('Official CMG hierarchy flag %g', hierarchy_flag)};
end

result = struct();
result.protocol_version = protocol_version;
result.run_id = getenv_default('CMG_RUN_ID', 'local');
result.task_id = str2double(getenv_default('CMG_TASK_ID', '1'));
result.source_commit = source_commit;
result.source_archive_sha256 = source_archive_sha256;
result.binary_sha256 = getenv_default('CMG_MEX_BINARY_SHA256', repmat('0', 1, 64));
result.environment_id = getenv_default('CMG_ENVIRONMENT_ID', repmat('0', 1, 64));
result.implementation = 'matlab';
result.experiment = getenv_default('CMG_EXPERIMENT', 'diagnostic');
result.family = metadata.family;
result.vertices = vertices;
result.canonical_edges = numel(weights);
result.matrix_nonzeros = nnz(A);
result.strategy = strategy;
result.actual_strategy = strategy;
result.route_reason = 'official-native-sequential-workflow';
result.hierarchy_threads = applied_threads;
result.plan_threads = 0;
result.solve_threads = applied_threads;
result.rhs_count = rhs_count;
result.warmups = warmups;
result.repetitions = repetitions;
result.tolerance = tolerance;
result.max_iterations = 1000;
result.input_load_ns = input_load_ns;
result.graph_assembly_ns = graph_assembly_ns;
result.apply_loops = apply_loops;
result.samples = samples;
result.phases = struct([]);
result.hierarchy = struct('levels', numel(H), 'vertices', level_vertices, 'matrix_nonzeros', level_nonzeros, 'repeats', repeat_counts, 'terminal_reason', matlab_terminal_reason(hierarchy_flag), 'plan_operator_count', 0, 'flag', hierarchy_flag);
result.numerical = struct('all_rhs', all_rhs, 'converged', all(final_flags == 0));
result.memory = struct('graph_bytes', whos_bytes('A'), 'hierarchy_bytes', whos_bytes('H'), 'terminal_factor_bytes', 0, 'plan_bytes', 0, 'workspace_bytes_each', 8 * vertices, 'workspace_pool_bytes', 8 * vertices, 'visible_bytes', visible_bytes, 'peak_rss_kb', peak_rss_kb());
result.placement = struct('mode', getenv_default('CMG_PLACEMENT', 'current'), 'cpu_list', getenv_default('CMG_CPU_LIST', ''), 'socket_list', getenv_default('CMG_SOCKET_LIST', ''), 'numa_node_list', getenv_default('CMG_NUMA_LIST', ''), 'memory_policy', getenv_default('CMG_MEMORY_POLICY', 'current'), 'first_touch_policy', getenv_default('CMG_FIRST_TOUCH', 'current'));
result.matlab_release = version('-release');
result.upstream_commit = upstream_commit;
result.requested_threads = requested_threads;
result.applied_threads = applied_threads;
result.warnings = warnings;
result.success = all(final_flags == 0) && all([all_rhs.backward_error] <= max(1.1 * tolerance, 1.1e-10));
if ~result.success
    error('cmg:scc2:convergence', 'MATLAB SCC2 numerical certification failed');
end

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
fprintf('CMG_SCC2_MATLAB_SUCCESS family=%s vertices=%d threads=%d rhs=%d\n', metadata.family, vertices, applied_threads, rhs_count);
end

function values = empty_samples()
values = struct('repetition', {}, 'measured', {}, 'order_position', {}, 'started_at_utc', {}, 'stage', {}, 'wall_ns', {}, 'process_cpu_ns', {});
end

function value = sample(repetition, order_position, started, stage, wall_ns, cpu_ns)
value = struct('repetition', repetition, 'measured', true, 'order_position', order_position, 'started_at_utc', started, 'stage', stage, 'wall_ns', wall_ns, 'process_cpu_ns', cpu_ns);
end

function value = utc_now()
value = char(datetime('now', 'TimeZone', 'UTC', 'Format', 'yyyy-MM-dd''T''HH:mm:ss.SSS''Z'''));
end

function value = getenv_default(name, fallback)
value = getenv(name);
if isempty(value)
    value = fallback;
end
end

function loops = calibrate_apply(pfun, rhs)
loops = 1;
while true
    stage_clock = tic;
    for loop = 1:loops
        output = pfun(rhs); %#ok<NASGU>
    end
    if toc(stage_clock) >= 2 || loops >= 1048576
        return
    end
    loops = loops * 2;
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
weight_bits = records(3, :);
weights = reshape(typecast(weight_bits, 'double'), [], 1);
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

function value = seconds_to_ns(seconds)
value = round(seconds * 1e9);
end

function value = centered_scaled_difference(solution, truth)
solution = solution - mean(solution);
truth = truth - mean(truth);
value = max(abs(solution - truth) ./ (1 + max(abs(solution), abs(truth))));
end

function [vertices, nonzeros, repeats] = hierarchy_metadata(H)
count = numel(H);
vertices = zeros(1, count);
nonzeros = zeros(1, count);
repeats = zeros(1, count);
for index = 1:count
    level = H{index};
    if isfield(level, 'A') && ~isempty(level.A)
        vertices(index) = size(level.A, 1);
        nonzeros(index) = nnz(level.A);
    elseif isfield(level, 'chol') && isfield(level.chol, 'ld')
        vertices(index) = size(level.chol.ld, 1) + 1;
        nonzeros(index) = nnz(level.chol.ld);
    end
    if isfield(level, 'repeat')
        repeats(index) = level.repeat;
    end
end
end

function value = matlab_terminal_reason(flag)
if flag == 0
    value = 'direct';
elseif flag == 1
    value = 'full_contraction';
elseif flag == 3
    value = 'stagnation';
else
    value = sprintf('flag_%g', flag);
end
end

function value = whos_bytes(name)
item = evalin('caller', sprintf('whos(''%s'')', name));
if isempty(item)
    value = 0;
else
    value = item.bytes;
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
