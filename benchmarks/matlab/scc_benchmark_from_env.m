function scc_benchmark_from_env()
%SCC_BENCHMARK_FROM_ENV Stable no-argument entry point for SGE batch jobs.
required = {'CMG_INPUT_DIR', 'CMG_THREADS', 'CMG_REPETITIONS', ...
    'CMG_MODE', 'CMG_OUTPUT_FILE', 'CMG_UPSTREAM_DIR', 'CMG_SOURCE_COMMIT'};
for index = 1:numel(required)
    if isempty(getenv(required{index}))
        error('cmg:benchmark:environment', 'Missing environment variable %s', required{index});
    end
end
upstream_dir = getenv('CMG_UPSTREAM_DIR');
addpath(upstream_dir);
addpath(fullfile(upstream_dir, 'mex'));
addpath(fileparts(mfilename('fullpath')));
scc_benchmark(getenv('CMG_INPUT_DIR'), str2double(getenv('CMG_THREADS')), ...
    str2double(getenv('CMG_REPETITIONS')), getenv('CMG_MODE'), ...
    getenv('CMG_OUTPUT_FILE'), getenv('CMG_SOURCE_COMMIT'));
end
