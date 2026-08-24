function scc_benchmark(input_dir, requested_threads, repetitions, mode, output_file, source_commit)
%SCC_BENCHMARK Run official CMG+MEX stages on the shared binary fixture.
arguments
    input_dir (1,:) char
    requested_threads (1,1) double {mustBeInteger,mustBePositive}
    repetitions (1,1) double {mustBeInteger,mustBePositive}
    mode (1,:) char {mustBeMember(mode, {'single','batch16'})}
    output_file (1,:) char
    source_commit (1,:) char
end

upstream_commit = '19752fc102f8cae8e34f66457bfaccb1aaa60375';
maxNumCompThreads(requested_threads);
applied_threads = maxNumCompThreads;

load_clock = tic;
[vertices, endpoints_u, endpoints_v, weights] = read_graph(fullfile(input_dir, 'graph.bin'));
right_hand_sides = read_vectors(fullfile(input_dir, 'rhs.bin'));
truths = read_vectors(fullfile(input_dir, 'truth.bin'));
metadata = jsondecode(fileread(fullfile(input_dir, 'metadata.json')));
input_load_ns = seconds_to_ns(toc(load_clock));
if size(right_hand_sides, 1) ~= vertices || ~isequal(size(right_hand_sides), size(truths))
    error('cmg:benchmark:input', 'RHS/truth dimensions do not match graph');
end
if strcmp(mode, 'batch16') && size(right_hand_sides, 2) < 16
    error('cmg:benchmark:input', 'batch16 requires sixteen right-hand sides');
end

assembly_clock = tic;
u = double(endpoints_u) + 1;
v = double(endpoints_v) + 1;
diagonal = accumarray([u; v], [weights; weights], [vertices 1], @sum, 0);
A = sparse([u; v; (1:vertices)'], [v; u; (1:vertices)'], ...
    [-weights; -weights; diagonal], vertices, vertices);
graph_build_ns = seconds_to_ns(toc(assembly_clock));
clear u v diagonal endpoints_u endpoints_v

opts = struct('validate', 0, 'matlab_', 0);
rhs = right_hand_sides(:, 1);
warm_preconditioner = cmg_precondition(A, opts);
warm_preconditioner(rhs);
clear warm_preconditioner

setup_samples_ns = zeros(1, repetitions);
pfun = [];
H = [];
hierarchy_flag = NaN;
for repetition = 1:repetitions
    stage_clock = tic;
    [candidate_pfun, candidate_H, candidate_flag] = cmg_precondition(A, opts);
    setup_samples_ns(repetition) = seconds_to_ns(toc(stage_clock));
    pfun = candidate_pfun;
    H = candidate_H;
    hierarchy_flag = candidate_flag;
end
if isempty(pfun)
    error('cmg:benchmark:preconditioner', 'CMG did not produce a preconditioner');
end

apply_loops = calibrate_apply(pfun, rhs);
apply_samples_ns = zeros(1, repetitions);
for repetition = 1:repetitions
    stage_clock = tic;
    for loop = 1:apply_loops
        apply_output = pfun(rhs); %#ok<NASGU>
    end
    apply_samples_ns(repetition) = seconds_to_ns(toc(stage_clock)) / apply_loops;
end

pcg(A, rhs, 1e-8, 1000, pfun, [], zeros(vertices, 1));
pcg_samples_ns = zeros(1, repetitions);
solver_total_samples_ns = zeros(1, repetitions);
final_solution = [];
final_flag = NaN;
final_relres = NaN;
final_iterations = NaN;
final_solutions = [];
batch_iterations = [];
batch_native_flags = [];
batch_native_relative_residuals = [];
batch_count = 1;
if strcmp(mode, 'single')
    for repetition = 1:repetitions
        stage_clock = tic;
        [candidate_solution, candidate_flag, candidate_relres, candidate_iterations] = ...
            pcg(A, rhs, 1e-8, 1000, pfun, [], zeros(vertices, 1));
        pcg_samples_ns(repetition) = seconds_to_ns(toc(stage_clock));
        final_solution = candidate_solution;
        final_flag = candidate_flag;
        final_relres = candidate_relres;
        final_iterations = candidate_iterations;
    end
    for repetition = 1:repetitions
        stage_clock = tic;
        local_pfun = cmg_precondition(A, opts);
        [~, local_flag] = pcg(A, rhs, 1e-8, 1000, local_pfun, [], zeros(vertices, 1));
        if local_flag ~= 0
            error('cmg:benchmark:convergence', 'Fresh setup-plus-solve failed with flag %g', local_flag);
        end
        solver_total_samples_ns(repetition) = seconds_to_ns(toc(stage_clock));
    end
else
    batch_count = 16;
    final_solutions = zeros(vertices, batch_count);
    batch_iterations = zeros(1, batch_count);
    batch_native_flags = zeros(1, batch_count);
    batch_native_relative_residuals = zeros(1, batch_count);
    for repetition = 1:repetitions
        stage_clock = tic;
        for rhs_index = 1:batch_count
            [candidate_solution, candidate_flag, candidate_relres, candidate_iterations] = ...
                pcg(A, right_hand_sides(:, rhs_index), 1e-8, 1000, pfun, [], zeros(vertices, 1));
            if rhs_index == 1
                final_solution = candidate_solution;
                final_flag = candidate_flag;
                final_relres = candidate_relres;
                final_iterations = candidate_iterations;
            end
            final_solutions(:, rhs_index) = candidate_solution;
            batch_iterations(rhs_index) = candidate_iterations;
            batch_native_flags(rhs_index) = candidate_flag;
            batch_native_relative_residuals(rhs_index) = candidate_relres;
        end
        pcg_samples_ns(repetition) = seconds_to_ns(toc(stage_clock));
    end
    % The compact batch supplement is intentionally setup-reused throughput.
    % Keep this schema field aligned with that timing block; it is not used as
    % the primary single-RHS setup-plus-solve measure.
    solver_total_samples_ns = pcg_samples_ns;
end

residual = rhs - A * final_solution;
residual_norm = norm(residual);
rhs_norm = norm(rhs);
relative_residual = residual_norm / max(rhs_norm, realmin);
operator_bound = 2 * max(full(diag(A)));
backward_error = residual_norm / max(operator_bound * norm(final_solution) + rhs_norm, realmin);
truth_scaled_error = centered_scaled_difference(final_solution, truths(:, 1));
if strcmp(mode, 'single')
    batch_iterations = final_iterations;
    batch_native_flags = final_flag;
    batch_native_relative_residuals = final_relres;
    batch_relative_residuals = relative_residual;
    batch_backward_errors = backward_error;
    batch_truth_scaled_errors = truth_scaled_error;
else
    batch_relative_residuals = zeros(1, batch_count);
    batch_backward_errors = zeros(1, batch_count);
    batch_truth_scaled_errors = zeros(1, batch_count);
    for rhs_index = 1:batch_count
        local_residual = right_hand_sides(:, rhs_index) - A * final_solutions(:, rhs_index);
        local_residual_norm = norm(local_residual);
        local_rhs_norm = norm(right_hand_sides(:, rhs_index));
        batch_relative_residuals(rhs_index) = local_residual_norm / max(local_rhs_norm, realmin);
        batch_backward_errors(rhs_index) = local_residual_norm / max( ...
            operator_bound * norm(final_solutions(:, rhs_index)) + local_rhs_norm, realmin);
        batch_truth_scaled_errors(rhs_index) = centered_scaled_difference( ...
            final_solutions(:, rhs_index), truths(:, rhs_index));
    end
end
[level_vertices, level_nonzeros, repeat_counts] = hierarchy_metadata(H);

result = struct();
result.schema = 1;
result.implementation = 'matlab';
result.source_commit = source_commit;
result.upstream_commit = upstream_commit;
result.matlab_release = version('-release');
result.family = metadata.family;
result.mode = mode;
result.vertices = vertices;
result.canonical_edges = numel(weights);
result.matrix_nonzeros = nnz(A);
result.threads = applied_threads;
result.requested_threads = requested_threads;
result.repetitions = repetitions;
result.batch_count = batch_count;
result.batch_iterations = batch_iterations;
result.batch_native_pcg_flags = batch_native_flags;
result.batch_native_relative_residuals = batch_native_relative_residuals;
result.batch_relative_residuals = batch_relative_residuals;
result.batch_backward_errors = batch_backward_errors;
result.batch_truth_scaled_errors = batch_truth_scaled_errors;
result.batch_max_backward_error = max(batch_backward_errors);
result.input_load_ns = input_load_ns;
result.graph_build_ns = graph_build_ns;
result.preconditioner_setup_samples_ns = setup_samples_ns;
result.preconditioner_setup_median_ns = median(setup_samples_ns);
result.parallel_plan_setup_samples_ns = zeros(1, repetitions);
result.parallel_plan_setup_median_ns = 0;
result.preconditioner_apply_loops = apply_loops;
result.preconditioner_apply_samples_ns = apply_samples_ns;
result.preconditioner_apply_median_ns = median(apply_samples_ns);
result.pcg_samples_ns = pcg_samples_ns;
result.pcg_median_ns = median(pcg_samples_ns);
result.solver_total_samples_ns = solver_total_samples_ns;
result.solver_total_median_ns = median(solver_total_samples_ns);
result.iterations = final_iterations;
result.native_pcg_flag = final_flag;
result.native_relative_residual = final_relres;
result.residual_norm = residual_norm;
result.relative_residual = relative_residual;
result.backward_error = backward_error;
result.truth_scaled_error = truth_scaled_error;
result.hierarchy_flag = hierarchy_flag;
result.levels = numel(H);
result.level_vertices = level_vertices;
result.level_matrix_nonzeros = level_nonzeros;
result.repeat_counts = repeat_counts;
result.plan_operators = 0;
result.plan_bytes = 0;
result.workspace_bytes = 0;
result.warnings = {};
if hierarchy_flag ~= 0
    result.warnings = {sprintf('Official CMG hierarchy flag %g', hierarchy_flag)};
end
result.native_converged = all(batch_native_flags == 0);
result.success = result.native_converged && all(isfinite(batch_backward_errors)) && ...
    max(batch_relative_residuals) <= 1e-7;
if ~result.success
    error('cmg:benchmark:convergence', ...
        'Official PCG failed certification: max flag=%g max relative residual=%g', ...
        max(batch_native_flags), max(batch_relative_residuals));
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
fprintf('CMG_BENCH_MATLAB_SUCCESS family=%s vertices=%d threads=%d mode=%s\n', ...
    metadata.family, vertices, applied_threads, mode);
end

function loops = calibrate_apply(pfun, rhs)
loops = 1;
while true
    stage_clock = tic;
    for loop = 1:loops
        output = pfun(rhs); %#ok<NASGU>
    end
    if toc(stage_clock) >= 1 || loops >= 1048576
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
value = seconds * 1e9;
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
